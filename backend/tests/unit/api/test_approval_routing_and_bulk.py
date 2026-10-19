"""Group routing, the self-approval block and bulk approve (ADR 0005 D6).

A gate routes an approval to a Databricks group by storing its display name in
``assigned_to_role``. Members (by ``User.entitlements``, case-insensitive) must
both see it in the inbox and be allowed to act on it.
"""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.v1 import requests as requests_api
from app.api.v1.approvals import get_approval, get_approvals
from app.api.v1.requests import (
    SELF_APPROVAL_DETAIL,
    BulkApproveIn,
    approve_request,
    approve_requests_bulk,
    reject_request,
)
from app.core.config import settings
from app.db import ApprovalModel, EventModel, RequestModel
from app.db.base import Base
from app.db.session import get_db
from app.models.user import User

GROUP = "UC Approvers"


def _user(email, roles=(), groups=()):
    return User(id=email, email=email, full_name=email, roles=list(roles),
                entitlements=[email, *groups], is_active=True)


ADMIN = _user("admin@example.com", roles=["Platform Admin"])
MEMBER = _user("member@example.com", groups=["uc approvers"])
OUTSIDER = _user("outsider@example.com", groups=["Other Group"])


def _add(db, request_id, requester="requester@example.com", role=GROUP, approval_type="data_owner"):
    db.add(RequestModel(id=request_id, type="rbac_access_change", title=f"Request {request_id}",
                        status="pending", requester_email=requester))
    db.add(ApprovalModel(id=f"appr-{request_id}", request_id=request_id, approval_type=approval_type,
                         status="pending", requested_by=requester, requested_by_email=requester,
                         assigned_to_role=role))
    db.commit()


def _status(db, request_id):
    db.expire_all()
    return db.get(ApprovalModel, f"appr-{request_id}").status


def _facts(db, request_id, fact_type="approval_received"):
    return db.query(EventModel).filter(EventModel.request_id == request_id,
                                       EventModel.event_type == fact_type).all()


@pytest.fixture(autouse=True)
def _deny_self_approval(monkeypatch):
    monkeypatch.setattr(settings, "APPROVALS_DENY_SELF_APPROVAL", True)


@pytest.fixture
def committing_db():
    """A database whose commits and rollbacks are real, like a request's session."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


# --- Routing to a group ----------------------------------------------------------


def test_group_member_sees_the_approval_case_insensitively(db_session):
    _add(db_session, "req-1")

    assert [a.requestId for a in get_approvals(status="pending", current_user=MEMBER, db=db_session)] == ["req-1"]
    assert get_approvals(status="pending", current_user=OUTSIDER, db=db_session) == []
    assert get_approval("appr-req-1", current_user=MEMBER, db=db_session).requestId == "req-1"
    with pytest.raises(HTTPException) as exc_info:
        get_approval("appr-req-1", current_user=OUTSIDER, db=db_session)
    assert exc_info.value.status_code == 403


def test_group_member_can_approve_and_outsider_cannot(db_session):
    _add(db_session, "req-1")
    _add(db_session, "req-2")

    with pytest.raises(HTTPException) as exc_info:
        approve_request("req-1", current_user=OUTSIDER, db=db_session)
    assert exc_info.value.status_code == 403

    approve_request("req-1", current_user=MEMBER, db=db_session)
    assert _status(db_session, "req-1") == "approved"

    reject_request("req-2", {"rejection_note": "no"}, current_user=MEMBER, db=db_session)
    assert _status(db_session, "req-2") == "rejected"


def test_no_group_configured_falls_back_to_platform_admins(db_session):
    _add(db_session, "req-1", role=None, approval_type="platform_admin")

    assert get_approvals(status="pending", current_user=MEMBER, db=db_session) == []
    with pytest.raises(HTTPException):
        approve_request("req-1", current_user=MEMBER, db=db_session)
    approve_request("req-1", current_user=ADMIN, db=db_session)
    assert _status(db_session, "req-1") == "approved"


def test_the_card_names_the_access_rule_approvers_for_the_requests_type(db_session):
    _add(db_session, "req-uc", approval_type="platform_admin")
    _add(db_session, "req-ws", role=None, approval_type="platform_admin")
    _add(db_session, "req-other")
    db_session.get(RequestModel, "req-uc").state_context = {"prepared_change": {"plane": "uc"}}
    db_session.get(RequestModel, "req-ws").state_context = {"items": [{"securable_type": "workspace"}]}
    db_session.commit()

    labels = {a.requestId: a.approverLabel
              for a in get_approvals(status="pending", current_user=ADMIN, db=db_session)}
    assert labels == {"req-uc": f"UC securables approvers: {GROUP}",
                      "req-ws": "Platform Admins (no Workspace securables group set)",
                      # Only the gate that takes its group from the type of rule gets a label.
                      "req-other": None}
    assert get_approval("appr-req-uc", current_user=MEMBER, db=db_session).approverLabel == (
        f"UC securables approvers: {GROUP}")

    # The prepared change is the step report's source, not a parameter to show again as JSON.
    card = get_approval("appr-req-uc", current_user=ADMIN, db=db_session)
    assert "prepared_change" not in (card.workflowParameters or {})

    # The requester's own view of the request names the same approvers, and its timeline
    # calls the gate by where its approvers come from rather than "platform admin".
    formatted = requests_api._format_request(db_session.get(RequestModel, "req-ws"), db_session)
    [shown] = formatted.approvals
    assert shown.approverLabel == "Platform Admins (no Workspace securables group set)"
    gates = {s.id: (s.gateType, s.gateLabel) for s in formatted.stateMachine.states if s.gateType}
    assert gates == {"approve": ("platform_admin", "Access rule approvers")}
    summary = requests_api._format_request(db_session.get(RequestModel, "req-ws"), db_session, summary=True)
    assert summary.approvals[0].approverLabel is None


# --- Self-approval ------------------------------------------------------------------


def test_nobody_approves_their_own_request_not_even_a_platform_admin(db_session):
    _add(db_session, "req-own", requester="Admin@Example.com")

    with pytest.raises(HTTPException) as exc_info:
        approve_request("req-own", current_user=ADMIN, db=db_session)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == SELF_APPROVAL_DETAIL
    assert _status(db_session, "req-own") == "pending"
    assert _facts(db_session, "req-own") == []


def test_rejecting_your_own_request_is_still_allowed(db_session):
    _add(db_session, "req-own", requester=MEMBER.email)

    reject_request("req-own", {"rejection_note": "withdrawn"}, current_user=MEMBER, db=db_session)
    assert _status(db_session, "req-own") == "rejected"


def test_self_approval_allowed_when_the_setting_is_off(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APPROVALS_DENY_SELF_APPROVAL", False)
    _add(db_session, "req-own", requester=ADMIN.email)

    approve_request("req-own", current_user=ADMIN, db=db_session)
    assert _status(db_session, "req-own") == "approved"


def test_inbox_flags_the_users_own_requests(db_session, monkeypatch):
    _add(db_session, "req-own", requester=MEMBER.email)
    _add(db_session, "req-other")

    flags = {a.requestId: (a.isOwnRequest, a.selfApprovalBlocked)
             for a in get_approvals(status="pending", current_user=MEMBER, db=db_session)}
    assert flags == {"req-own": (True, True), "req-other": (False, False)}

    monkeypatch.setattr(settings, "APPROVALS_DENY_SELF_APPROVAL", False)
    own = get_approval("appr-req-own", current_user=MEMBER, db=db_session)
    assert (own.isOwnRequest, own.selfApprovalBlocked) == (True, False)


# --- Bulk approve ------------------------------------------------------------------


def test_bulk_approve_reports_each_request_and_isolates_failures(committing_db, monkeypatch):
    db = committing_db
    _add(db, "req-ok")
    _add(db, "req-own", requester=MEMBER.email)
    _add(db, "req-theirs", role="Other Approvers")
    _add(db, "req-boom")
    _add(db, "req-ok-2")

    real_add_fact = requests_api.add_fact

    def flaky_add_fact(db_, request_id, *args, **kwargs):
        if request_id == "req-boom":
            raise RuntimeError("database hiccup")
        return real_add_fact(db_, request_id, *args, **kwargs)

    monkeypatch.setattr(requests_api, "add_fact", flaky_add_fact)

    out = approve_requests_bulk(
        BulkApproveIn(request_ids=["req-ok", "req-own", "req-missing", "req-theirs", "req-boom",
                                   "req-ok-2", "req-ok"],
                      comment="  Quarterly review  "),
        current_user=MEMBER, db=db,
    )

    got = [(r.request_id, r.ok, r.status_code) for r in out.results]
    assert got == [
        ("req-ok", True, 200),
        ("req-own", False, 403),
        ("req-missing", False, 404),
        ("req-theirs", False, 403),
        ("req-boom", False, 500),
        ("req-ok-2", True, 200),
    ]
    assert out.results[1].detail == SELF_APPROVAL_DETAIL

    assert _status(db, "req-ok") == "approved"
    assert _status(db, "req-ok-2") == "approved"
    # The failed write rolled back its own claim, and nothing else.
    assert _status(db, "req-boom") == "pending"
    assert _status(db, "req-own") == "pending"
    assert _status(db, "req-theirs") == "pending"
    facts = _facts(db, "req-ok")
    assert len(facts) == 1
    assert facts[0].event_data["comment"] == "Quarterly review"
    assert facts[0].event_data["approved_by"] == MEMBER.email


def test_bulk_approve_limits_the_batch():
    BulkApproveIn(request_ids=[f"r{i}" for i in range(100)])
    with pytest.raises(ValidationError):
        BulkApproveIn(request_ids=[f"r{i}" for i in range(101)])
    with pytest.raises(ValidationError):
        BulkApproveIn(request_ids=[])


def test_bulk_approve_route_is_reachable(committing_db):
    """``/approve-bulk`` must not be captured by the ``/{request_id}/...`` routes."""
    _add(committing_db, "req-ok")
    app = FastAPI()
    app.include_router(requests_api.router, prefix="/api/v1/requests")
    app.dependency_overrides[get_current_user] = lambda: MEMBER
    app.dependency_overrides[get_db] = lambda: committing_db

    with TestClient(app) as client:
        res = client.post("/api/v1/requests/approve-bulk", json={"request_ids": ["req-ok"]})
        assert res.status_code == 200
        assert res.json()["results"][0] == {
            "request_id": "req-ok", "ok": True, "status_code": 200,
            "detail": "Approval recorded. State will be updated by poller.",
        }
        too_many = client.post("/api/v1/requests/approve-bulk",
                               json={"request_ids": [f"r{i}" for i in range(101)]})
        assert too_many.status_code == 422
