"""No DB connection may be held across a long-running workflow step.

SQLAlchemy checks a pooled connection out when a transaction begins -- any read
will do, even one behind a log line -- and holds it until that transaction ends.
An Enforcement Sentinel scan runs for minutes and does all its own database work
on separate short-lived sessions, so a session left mid-transaction across it
pins a Lakebase connection idle-in-transaction for the whole run. Lakebase closes
it, and the next write fails with "SSL connection has been closed unexpectedly".

That one dropped connection then cascades: the poller's failure handler can't
write either, the incremented retry_count never lands, and the request is
re-selected at the same count on the next tick -- forever. The fresh-session
fallbacks in ``poller`` survive it; these tests keep it from happening.

The invariant spans three modules, so the tests live together:
  - ``poller``   releases before the graph advance/resume
  - ``tools``    releases right after loading the request row for a step
  - ``sentinel`` refuses a session outright
"""
import inspect
from types import SimpleNamespace

import pytest

from app.workers import poller
from app.workflows import sentinel, tools


class RecordingSession:
    """Session stand-in that records the order of transaction boundaries."""

    def __init__(self, events, row=None):
        self.events = events
        self.row = row
        self.expire_on_commit = True

    def query(self, _model):
        self.events.append("query")
        return self

    def filter(self, *_args):
        return self

    def first(self):
        return self.row

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")

    def refresh(self, _obj):
        self.events.append("refresh")

    def close(self):
        self.events.append("close")


async def _no_resume(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_poller_releases_its_connection_before_advancing_the_graph(monkeypatch):
    events = []
    db = RecordingSession(events)
    request = SimpleNamespace(id="req-1", status="processing", current_state=None)

    async def fake_advance(_request):
        events.append("advance")
        return SimpleNamespace(interrupted=False, status="running", current_node="scan")

    monkeypatch.setattr(
        "app.workflows.executor.executor", SimpleNamespace(advance=fake_advance)
    )
    monkeypatch.setattr("app.workflows.executor.to_request_status", lambda _s: None)
    monkeypatch.setattr(poller, "_v2_resume_value", _no_resume)

    await poller._process_request_state_machine(db, request)

    # The commit that returns the connection to the pool has to happen BEFORE the
    # multi-minute call, not after it.
    assert events.index("commit") < events.index("advance")
    # Otherwise that commit expires `request`, and advance()'s own attribute
    # reads lazily reload it -- re-opening the transaction just closed.
    assert db.expire_on_commit is False
    # The row is re-read afterwards: the graph moves it on through its own
    # sessions, so the pre-scan values are stale by the time we compare status.
    assert events.index("refresh") > events.index("advance")


@pytest.mark.asyncio
async def test_poller_release_failure_does_not_stop_the_advance(monkeypatch):
    """A release is an optimization; failing it must not fail the run."""
    events = []

    class UncommittableSession(RecordingSession):
        def commit(self):
            events.append("commit-failed")
            raise RuntimeError("connection already gone")

    db = UncommittableSession(events)

    async def fake_advance(_request):
        events.append("advance")
        return SimpleNamespace(interrupted=False, status="running", current_node="scan")

    monkeypatch.setattr(
        "app.workflows.executor.executor", SimpleNamespace(advance=fake_advance)
    )
    monkeypatch.setattr("app.workflows.executor.to_request_status", lambda _s: None)
    monkeypatch.setattr(poller, "_v2_resume_value", _no_resume)

    await poller._process_request_state_machine(
        db, SimpleNamespace(id="req-1", status="processing", current_state=None)
    )

    assert "advance" in events
    assert "rollback" in events


def test_load_request_leaves_no_open_transaction(monkeypatch):
    events = []
    row = SimpleNamespace(id="req-1")
    session = RecordingSession(events, row=row)
    monkeypatch.setattr("app.db.session.get_db", lambda: iter([session]))

    db, request = tools._load_request("req-1")

    assert request is row
    # Loaded, then the read transaction closed straight away -- the step that
    # follows can run for minutes without pinning the connection.
    assert events == ["query", "commit"]
    assert db.expire_on_commit is False


def test_load_request_with_no_id_touches_no_session(monkeypatch):
    monkeypatch.setattr(
        "app.db.session.get_db",
        lambda: pytest.fail("no session should be opened without a request id"),
    )
    assert tools._load_request(None) == (None, None)


def test_discovery_refuses_a_database_session():
    """A session parameter here is the bug, so the signature is the guard.

    ``_scan_and_evaluate`` used to take a ``db`` it never touched, and
    ``run_discovery`` threaded the poller's session down into it -- which is what
    held a connection open for the length of a scan. Re-adding either parameter
    would silently reintroduce that, so pin it.
    """
    assert "db" not in inspect.signature(sentinel.run_discovery).parameters
    assert "db" not in inspect.signature(sentinel._scan_and_evaluate).parameters
