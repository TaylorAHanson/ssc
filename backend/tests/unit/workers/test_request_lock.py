"""Request locks: acquire is exclusive, and release only clears the caller's own lock.

A run can outlive its lock (e.g. a missed heartbeat) and another worker can take
over; the first worker's release must not then clear the new holder's lock.
"""

from datetime import datetime, timedelta

from app.db import RequestModel
from app.state_machines.lock import acquire_lock, heartbeat_lock, release_lock


def _request(db, **fields):
    req = RequestModel(id="req-lock", type="t", title="t", status="pending", **fields)
    db.add(req)
    db.commit()
    return req


def _holder(db):
    db.expire_all()
    return db.query(RequestModel).filter(RequestModel.id == "req-lock").one().locked_by


def test_acquire_is_exclusive_until_expiry(db_session):
    _request(db_session)

    assert acquire_lock(db_session, "req-lock", "worker-a", timeout_minutes=5)
    assert not acquire_lock(db_session, "req-lock", "worker-b", timeout_minutes=5)
    assert _holder(db_session) == "worker-a"


def test_release_by_holder_clears_the_lock(db_session):
    _request(db_session)
    acquire_lock(db_session, "req-lock", "worker-a")

    release_lock(db_session, "req-lock", worker_id="worker-a")

    assert _holder(db_session) is None


def test_stale_holder_cannot_release_the_new_holders_lock(db_session):
    # worker-a's lock expired and worker-b took over.
    _request(db_session, locked_by="worker-a", locked_until=datetime.utcnow() - timedelta(minutes=1))
    assert acquire_lock(db_session, "req-lock", "worker-b")

    # worker-a finally finishes and runs its `finally: release_lock(...)`.
    release_lock(db_session, "req-lock", worker_id="worker-a")

    assert _holder(db_session) == "worker-b"
    assert not acquire_lock(db_session, "req-lock", "worker-c")


def test_release_without_worker_id_is_unconditional(db_session):
    _request(db_session)
    acquire_lock(db_session, "req-lock", "worker-a")

    release_lock(db_session, "req-lock")

    assert _holder(db_session) is None


def test_heartbeat_only_extends_own_lock(db_session):
    _request(db_session)
    acquire_lock(db_session, "req-lock", "worker-a")

    assert heartbeat_lock(db_session, "req-lock", "worker-a", timeout_minutes=5)
    assert not heartbeat_lock(db_session, "req-lock", "worker-b", timeout_minutes=5)
