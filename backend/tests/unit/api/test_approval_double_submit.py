"""Approve/reject act on a pending approval at most once.

Handlers run concurrently in the threadpool, so two calls (a double-click,
approve racing reject, or two replicas) can both read the same pending approval.
The status change is a compare-and-set: the loser gets a 409 and records no fact.
"""

import pytest
from fastapi import HTTPException

from app.api.v1.requests import _claim_pending_approval, approve_request, reject_request
from app.db import ApprovalModel, EventModel, RequestModel
from app.models.user import User
from tests.factories.approval_factory import ApprovalFactory

ADMIN = User(id="admin@example.com", email="admin@example.com", full_name="Admin",
             roles=["Platform Admin"], is_active=True)


@pytest.fixture
def pending(db_session):
    db_session.add(RequestModel(id="req-a", type="t", title="t", status="pending",
                                requester_email="user@example.com"))
    db_session.commit()
    return ApprovalFactory.create(db_session, "req-a", id="appr-1")


def _facts(db, fact_type):
    return db.query(EventModel).filter(EventModel.request_id == "req-a",
                                       EventModel.event_type == fact_type).count()


def test_approve_records_one_fact_and_second_call_finds_nothing_pending(db_session, pending):
    approve_request("req-a", current_user=ADMIN, db=db_session)

    db_session.expire_all()
    assert db_session.get(ApprovalModel, "appr-1").status == "approved"
    assert _facts(db_session, "approval_received") == 1
    with pytest.raises(HTTPException) as exc_info:
        approve_request("req-a", current_user=ADMIN, db=db_session)
    assert exc_info.value.status_code == 404
    assert _facts(db_session, "approval_received") == 1


def test_reject_records_note(db_session, pending):
    reject_request("req-a", {"rejection_note": "no"}, current_user=ADMIN, db=db_session)

    db_session.expire_all()
    appr = db_session.get(ApprovalModel, "appr-1")
    assert (appr.status, appr.rejected_by, appr.rejection_note) == ("rejected", "admin@example.com", "no")
    assert _facts(db_session, "request_rejected") == 1


def test_claim_loses_when_another_call_already_acted(db_session, pending):
    # A racing call read the approval while it was pending...
    stale = db_session.get(ApprovalModel, "appr-1")
    # ...but another call committed first.
    db_session.query(ApprovalModel).filter(ApprovalModel.id == "appr-1").update({"status": "approved"})
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        _claim_pending_approval(db_session, stale, {"status": "rejected", "rejected_by": "x"})

    assert exc_info.value.status_code == 409
    db_session.expire_all()
    assert db_session.get(ApprovalModel, "appr-1").status == "approved"


def test_claim_wins_on_a_pending_approval(db_session, pending):
    _claim_pending_approval(db_session, pending, {"status": "approved", "approved_by": "a"})
    db_session.commit()

    db_session.expire_all()
    assert db_session.get(ApprovalModel, "appr-1").approved_by == "a"
