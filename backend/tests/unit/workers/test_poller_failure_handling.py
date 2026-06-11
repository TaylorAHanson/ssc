"""Unit tests for the poller's retryable/permanent error handling.

These cover the contract the user cares about: a retryable error escalates to a
*permanent* failure once it has exhausted its retry budget, a permanent error
fails immediately, and neither path leaves a request in a state the poller would
keep re-selecting (which previously caused an infinite loop when the failure
handler itself crashed with an ``UnboundLocalError``).
"""
import pytest

from app.workers import poller
from app.models.request import RequestStatus
from app.core.exceptions import RetryableError, PermanentError
from tests.factories.request_factory import RequestFactory


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
