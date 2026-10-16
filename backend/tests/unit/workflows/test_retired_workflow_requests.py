"""Requests whose workflow no longer exists still open everywhere.

When a workflow is retired (its bundled spec deleted and its DB rows unpublished),
its historical requests keep their ``type``. The list, detail, timeline, graph and
approvals views must render them from the request's own data instead of erroring
or claiming a run reached a step it never did.
"""
from datetime import datetime

import orjson
import pytest

from app.api.v1.approvals import get_approvals
from app.api.v1.requests import get_paginated_requests, get_request, get_request_graph
from app.db import RequestModel
from app.db.approval import ApprovalModel
from app.models.user import User
from app.workflows import render
from app.workflows.graphs.specs import SPECS

RETIRED_TYPE = "data_access_request"
ADMIN = User(id="admin", email="admin@example.com", full_name="Admin",
             roles=["Platform Admin"], is_active=True)
WHEN = datetime(2026, 5, 1, 9, 0, 0)


class _Req:
    def __init__(self, status):
        self.id = "req-retired"
        self.status = status
        self.type = RETIRED_TYPE
        self.requester_email = "u@corp.com"
        self.created_at = WHEN


@pytest.fixture
def no_spec(monkeypatch):
    assert RETIRED_TYPE not in SPECS
    monkeypatch.setattr("app.state_machines.facts.get_facts", lambda db, rid: [])
    monkeypatch.setattr("app.workflows.graphs.published_graph_spec", lambda db, rt: None)


def _timeline(status):
    sm = render.render_state(_Req(status), db=None)
    return sm, [(s.id, s.isActive, s.isCompleted) for s in sm.states]


def test_completed_request_shows_created_then_completed(no_spec):
    sm, states = _timeline("completed")
    assert sm.currentState == "completed"
    assert states == [("pending", False, True), ("completed", True, True)]


def test_rejected_request_ends_on_rejected(no_spec):
    sm, states = _timeline("rejected")
    assert sm.currentState == "rejected"
    assert states[-1] == ("rejected", True, False)


def test_in_flight_request_shows_its_last_status_not_completed(no_spec):
    sm, states = _timeline("data_owner_approval")
    assert sm.currentState == "data_owner_approval"
    assert states == [
        ("pending", False, True),
        ("data_owner_approval", True, False),
        ("completed", False, False),
    ]
    assert sm.states[1].name == "Data Owner Approval"


def test_pending_request_is_still_at_created(no_spec):
    sm, states = _timeline("pending")
    assert sm.currentState == "pending"
    assert states == [("pending", True, False), ("completed", False, False)]


def test_live_graph_is_empty_not_an_error(no_spec):
    out = render.live_graph(_Req("completed"), db=None)
    assert out["graph_spec"]["stages"] == []
    assert out["request_type"] == RETIRED_TYPE


@pytest.fixture
def retired_request(db_session):
    db_session.add(RequestModel(
        id="req-retired", type=RETIRED_TYPE, title="Read access to main.sales.orders",
        status="data_owner_approval", current_state="data_owner_approval",
        requester_email="u@corp.com",
        state_context={"asset_name": "main.sales.orders", "access_level": "read"},
        created_at=WHEN, updated_at=WHEN,
    ))
    db_session.add(ApprovalModel(
        id="appr-retired", request_id="req-retired", approval_type="data_owner",
        requested_by_email="u@corp.com", assigned_to_email=ADMIN.email,
        status="pending", created_at=WHEN, updated_at=WHEN,
    ))
    db_session.commit()


def test_detail_endpoint_renders_timeline_and_approvals(db_session, retired_request):
    body = orjson.loads(get_request("req-retired", current_user=ADMIN, db=db_session).body)
    assert body["type"] == RETIRED_TYPE
    assert body["metadata"]["asset_name"] == "main.sales.orders"
    assert [s["id"] for s in body["stateMachine"]["states"]] == [
        "pending", "data_owner_approval", "completed"]
    assert [a["id"] for a in body["approvals"]] == ["appr-retired"]


def test_graph_endpoint_returns_no_stages(db_session, retired_request):
    out = get_request_graph("req-retired", current_user=ADMIN, db=db_session)
    assert out["graph_spec"]["stages"] == []
    assert out["status"] == "data_owner_approval"


def test_my_requests_list_includes_it(db_session, retired_request):
    resp = get_paginated_requests(
        skip=0, limit=50, type=None, search=None, summary=True,
        exclude_type=None, created_after=None, current_user=ADMIN, db=db_session,
    )
    items = {i["id"]: i for i in orjson.loads(resp.body)["items"]}
    assert items["req-retired"]["type"] == RETIRED_TYPE
    assert items["req-retired"]["stateMachine"]["currentState"] == "data_owner_approval"


def test_approvals_inbox_includes_it(db_session, retired_request):
    rows = get_approvals(current_user=ADMIN, db=db_session)
    row = next(r for r in rows if r.id == "appr-retired")
    assert (row.requestType, row.approvalType) == (RETIRED_TYPE, "data_owner")
