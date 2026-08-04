"""Unit tests for the poller's retryable/permanent error handling.

These cover the contract the user cares about: a retryable error escalates to a
*permanent* failure once it has exhausted its retry budget, a permanent error
fails immediately, and neither path leaves a request in a state the poller would
keep re-selecting (which previously caused an infinite loop when the failure
handler itself crashed with an ``UnboundLocalError``).

The ``dead connection`` group below covers the other way this loops: a long
Sentinel scan outlives its Lakebase connection, so the *failure handler's own*
write fails and the incremented retry_count is never persisted.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app.workers import poller
from app.models.request import RequestStatus
from app.core.exceptions import RetryableError, PermanentError
from tests.factories.request_factory import RequestFactory


def dead_connection_error():
    """The production failure: Lakebase dropped the SSL connection mid-scan."""
    return OperationalError(
        "UPDATE requests SET last_failure=%(last_failure)s, retry_count=%(retry_count)s",
        {},
        Exception("SSL connection has been closed unexpectedly"),
    )


class DeadSession:
    """A session whose underlying connection has died: every commit raises."""

    def __init__(self):
        self.rollbacks = 0
        self.commits_attempted = 0

    def add(self, _obj):
        pass

    def commit(self):
        self.commits_attempted += 1
        raise dead_connection_error()

    def rollback(self):
        self.rollbacks += 1


def fake_request(**overrides):
    base = dict(
        id="req-1",
        retry_count=0,
        max_retries=3,
        status="processing",
        last_failure=None,
        last_error=None,
        type="enforcement_sentinel",
        title="Sentinel Run",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_retryable_error_retries_within_budget(db_session):
    req = RequestFactory.create(
        db_session, status="processing", retry_count=0, max_retries=3
    )

    await poller._handle_retryable_error(
        db_session, req, RetryableError("transient blip"), "worker-1"
    )

    db_session.refresh(req)
    assert req.retry_count == 1
    # Still within budget -> stays processing for the next poll cycle.
    assert req.status == "processing"
    assert req.last_error["error"] == "transient blip"


@pytest.mark.asyncio
async def test_retryable_error_escalates_to_permanent_when_exhausted(db_session):
    # One attempt away from the cap.
    req = RequestFactory.create(
        db_session, status="processing", retry_count=2, max_retries=3
    )

    await poller._handle_retryable_error(
        db_session, req, RetryableError("keeps failing"), "worker-1"
    )

    db_session.refresh(req)
    assert req.status == RequestStatus.FAILED.value
    # Budget is exhausted so the poller's max-retries guard skips it from now on.
    assert req.retry_count >= req.max_retries
    assert req.last_error["permanent"] is False


@pytest.mark.asyncio
async def test_permanent_error_fails_immediately(db_session):
    req = RequestFactory.create(
        db_session, status="processing", retry_count=0, max_retries=3
    )

    # The previous UnboundLocalError bug made this raise instead of failing the
    # request, so simply completing without an exception is the regression check.
    await poller._handle_permanent_error(
        db_session, req, PermanentError("not recoverable"), "worker-1"
    )

    db_session.refresh(req)
    assert req.status == RequestStatus.FAILED.value
    assert req.retry_count >= req.max_retries
    assert req.last_error["permanent"] is True


def test_ensure_approval_task_creates_pending_row_for_human_gate(db_session):
    from app.db.approval import ApprovalModel

    req = RequestFactory.create(
        db_session, type="data_access_request", status="data_owner_approval",
        requester_email="alice@corp.com",
    )
    payload = {"type": "data_owner", "gate": "await_approval",
               "request_id": req.id, "approvers": ["governance_team"]}

    poller._ensure_approval_task(db_session, req, payload)

    rows = db_session.query(ApprovalModel).filter_by(request_id=req.id).all()
    assert len(rows) == 1
    appr = rows[0]
    assert appr.approval_type == "data_owner"
    assert appr.status == "pending"
    # A non-email approver is stored as a role/group, not an assignee email.
    assert appr.assigned_to_role == "governance_team"
    assert appr.assigned_to_email is None
    assert appr.requested_by_email == "alice@corp.com"

    # Idempotent: a second call (next poll tick) doesn't duplicate the task.
    poller._ensure_approval_task(db_session, req, payload)
    assert db_session.query(ApprovalModel).filter_by(request_id=req.id).count() == 1


def test_ensure_approval_task_routes_email_approver_as_assignee(db_session):
    from app.db.approval import ApprovalModel

    req = RequestFactory.create(db_session, status="data_owner_approval")
    payload = {"type": "data_owner", "approvers": ["owner@corp.com"]}

    poller._ensure_approval_task(db_session, req, payload)

    appr = db_session.query(ApprovalModel).filter_by(request_id=req.id).one()
    assert appr.assigned_to_email == "owner@corp.com"
    assert appr.assigned_to_role is None


def test_ensure_approval_task_skips_automated_gates(db_session):
    from app.db.approval import ApprovalModel

    req = RequestFactory.create(db_session, status="provisioning")
    # training / pr_merge / children resolve via facts, not a human approval row.
    poller._ensure_approval_task(db_session, req, {"type": "training"})
    poller._ensure_approval_task(db_session, req, {"type": "pr_merge"})

    assert db_session.query(ApprovalModel).filter_by(request_id=req.id).count() == 0


@pytest.mark.asyncio
async def test_mark_failed_sets_failed_state_in_memory(db_session):
    """``_mark_request_failed`` sets FAILED + exhausted retries on the instance.

    (The audit-row-failure recovery path performs an internal rollback, which the
    single-transaction test fixture can't model, so this asserts the in-memory
    state the poller relies on rather than re-exercising that recovery branch.)
    """
    req = RequestFactory.create(
        db_session, status="processing", retry_count=1, max_retries=3
    )

    poller._mark_request_failed(
        db_session, req, PermanentError("boom"), "worker-1",
        failure_type="permanent_error", permanent=True,
    )

    assert req.status == RequestStatus.FAILED.value
    assert req.retry_count >= req.max_retries
    assert req.last_error["permanent"] is True


# --- Recovery when the held connection died mid-scan -------------------------
#
# A multi-workspace Sentinel scan can run for minutes with the poller's session
# idle, long enough for Lakebase to drop it. The failure handler then can't write
# either, and rollback()+recommit on the same session cannot revive a dead
# connection. If the incremented retry_count never lands, the poller re-selects
# the request at the same count on the next tick -- forever. That is how one
# dropped connection became a flood of identical "Sentinel Run Failed" reports.


async def _noop_async(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_retryable_error_records_on_a_fresh_session_when_connection_died(
    monkeypatch,
):
    dead = DeadSession()
    req = fake_request(retry_count=0, max_retries=3)
    recorded = {}
    monkeypatch.setattr(
        poller,
        "_record_retry_attempt_on_fresh_session",
        lambda rid, err, wid, rc: recorded.update(
            id=rid, error=err, worker=wid, retry=rc
        ),
    )

    original = RetryableError("workspace scan timed out")
    await poller._handle_retryable_error(dead, req, original, "worker-1")

    assert recorded["id"] == "req-1"
    assert recorded["worker"] == "worker-1"
    # Exactly one increment. The old recovery path incremented a second time
    # while retrying on the same session, so a single failure burned two
    # attempts out of the budget.
    assert recorded["retry"] == 1
    # The ORIGINAL cause is what gets persisted. Previously the DB plumbing
    # error replaced it, and the run report showed a psycopg2 SSL traceback
    # instead of the thing that actually went wrong.
    assert recorded["error"] is original


@pytest.mark.asyncio
async def test_retryable_error_does_not_raise_when_even_rollback_fails(monkeypatch):
    """A dead connection can fail the rollback too; that must not escape.

    It used to propagate out of the handler into the poller's critical-error
    guard, which force-failed the request while recording the DB error as the
    cause.
    """
    dead = DeadSession()
    dead.rollback = lambda: (_ for _ in ()).throw(dead_connection_error())
    monkeypatch.setattr(
        poller, "_record_retry_attempt_on_fresh_session", lambda *a: True
    )

    await poller._handle_retryable_error(
        dead, fake_request(), RetryableError("blip"), "worker-1"
    )


@pytest.mark.asyncio
async def test_exhausted_retries_force_fail_on_fresh_session_when_connection_died(
    monkeypatch,
):
    dead = DeadSession()
    # One attempt away from the cap, so this escalates through _mark_request_failed.
    req = fake_request(retry_count=2, max_retries=3)
    forced = {}
    monkeypatch.setattr(
        poller,
        "_force_fail_on_fresh_session",
        lambda rid, err, wid: forced.update(id=rid, error=err, worker=wid),
    )
    monkeypatch.setattr(poller, "_send_failure_notification", _noop_async)

    original = RetryableError("keeps failing")
    await poller._handle_retryable_error(dead, req, original, "worker-1")

    # Without this the request stays non-terminal, is re-selected next tick, and
    # loops despite having exhausted its budget.
    assert forced["id"] == "req-1"
    assert forced["error"] is original


@pytest.mark.asyncio
async def test_failure_notification_survives_an_expired_instance():
    """Reading a column off an expired instance hits the dead connection.

    ``request.type`` was read outside the try block and the except clause
    reached for ``request.id`` a second time, so the notification about the
    failure raised its own error on exactly the path it exists to report.
    """

    class ExpiredInstance:
        def __getattr__(self, _name):
            raise dead_connection_error()

    await poller._send_failure_notification(ExpiredInstance(), "boom")


def test_record_retry_attempt_writes_the_increment_on_a_new_session(monkeypatch):
    row = SimpleNamespace(retry_count=0, last_failure=None, last_error=None)
    session = SimpleNamespace(
        committed=False,
        closed=False,
        query=lambda _m: SimpleNamespace(
            filter=lambda *_a: SimpleNamespace(first=lambda: row)
        ),
    )
    session.commit = lambda: setattr(session, "committed", True)
    session.close = lambda: setattr(session, "closed", True)
    monkeypatch.setattr(poller, "get_lakebase_session", lambda: session)

    ok = poller._record_retry_attempt_on_fresh_session(
        "req-1", RetryableError("blip"), "worker-1", 2
    )

    assert ok is True
    assert session.committed is True
    assert session.closed is True
    assert row.retry_count == 2
    assert row.last_error["error"] == "blip"
    assert row.last_error["retry_count"] == 2


def test_record_retry_attempt_reports_failure_rather_than_raising(monkeypatch):
    """The last-resort write can fail too; callers rely on a bool, not a raise."""
    session = SimpleNamespace(
        query=lambda _m: SimpleNamespace(
            filter=lambda *_a: SimpleNamespace(
                first=lambda: (_ for _ in ()).throw(dead_connection_error())
            )
        ),
        rollback=lambda: None,
        close=lambda: None,
    )
    monkeypatch.setattr(poller, "get_lakebase_session", lambda: session)

    assert (
        poller._record_retry_attempt_on_fresh_session(
            "req-1", RetryableError("blip"), "worker-1", 1
        )
        is False
    )
