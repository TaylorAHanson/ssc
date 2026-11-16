"""Reporting a data issue starts the data-issue workflow as the signed-in user."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1 import data_assets as routes
from app.db.data_asset import DataAssetModel
from app.db.request import RequestModel
from tests.factories.discover_domain_factory import add_domain

USER = SimpleNamespace(email="rae@example.com", full_name="Rae Reporter", roles=[], entitlements=[])


def _asset(db, **fields):
    fields.setdefault("tags", [])
    asset = DataAssetModel(id="main.sales.orders", catalog="main", schema="sales", table_name="orders",
                           type="MANAGED", owner="3f1c9a7e-sp-uuid", **fields)
    db.add(asset)
    db.flush()
    return asset


def _report(db, asset_id="main.sales.orders", summary="Row counts dropped", details=None):
    body = routes.ReportDataIssue(asset_id=asset_id, summary=summary, details=details)
    return routes.report_data_issue(body=body, current_user=USER, db=db)


def test_the_report_starts_the_workflow_with_the_asset_and_its_owner(db_session):
    _asset(db_session, tags=["Sales"])
    add_domain(db_session, "Sales", business_owners=[{"id": 1, "name": "Dana Domain"}])

    out = _report(db_session, details="Down 40% since Monday")

    request = db_session.query(RequestModel).filter_by(id=out["request_id"]).one()
    assert request.type == "data_issue_report"
    assert request.title == "Data issue: main.sales.orders"
    assert request.requester_email == "rae@example.com"
    ctx = request.state_context
    assert ctx["owner"] == "Dana Domain" and out["owner"] == "Dana Domain"
    assert ctx["_email_subject"] == "Data issue reported: main.sales.orders"
    assert "Row counts dropped" in ctx["_email_body"] and "Down 40% since Monday" in ctx["_email_body"]
    assert "Dana Domain" in ctx["_email_body"] and "Rae Reporter" in ctx["_email_body"]


def test_the_owner_is_the_assets_own_when_no_domain_names_one(db_session):
    _asset(db_session)
    assert _report(db_session)["owner"] == "3f1c9a7e-sp-uuid"


def test_what_the_user_typed_cannot_inject_html_into_the_email(db_session):
    _asset(db_session)

    out = _report(db_session, summary="<script>alert(1)</script> broken", details="line one\n<b>line</b> two")

    body = db_session.query(RequestModel).filter_by(id=out["request_id"]).one().state_context["_email_body"]
    assert "<script>" not in body and "&lt;script&gt;" in body
    assert "<b>line</b>" not in body and "&lt;b&gt;line&lt;/b&gt;" in body
    assert "line one<br>" in body  # line breaks survive


def test_an_unknown_asset_is_a_404(db_session):
    with pytest.raises(HTTPException) as err:
        _report(db_session, asset_id="main.sales.nope")
    assert err.value.status_code == 404
    assert db_session.query(RequestModel).count() == 0


def test_a_hidden_asset_looks_like_a_missing_one(db_session):
    _asset(db_session)
    hidden = SimpleNamespace(is_hidden_id=lambda asset_id: True)
    with patch.object(routes, "load_visibility", return_value=hidden):
        with pytest.raises(HTTPException) as err:
            _report(db_session)
    assert err.value.status_code == 404


def test_a_retired_workflow_says_so_instead_of_failing(db_session):
    _asset(db_session)
    with patch("app.services.workflow_service.WorkflowService.is_known_request_type", return_value=False):
        with pytest.raises(HTTPException) as err:
            _report(db_session)
    assert err.value.status_code == 503
    assert "isn't set up" in err.value.detail


def test_a_blank_summary_is_refused():
    with pytest.raises(ValidationError):
        routes.ReportDataIssue(asset_id="main.sales.orders", summary="     ")


def test_the_workflow_reads_the_email_and_the_hidden_keys_the_endpoint_fills_in(db_session):
    """The bundled workflow's email step takes its subject and body from the keys the endpoint stores,
    and approvers are not shown those keys."""
    from app.api.v1.approvals import _get_workflow_params
    from app.workflows.graphs.specs import _load_catalog
    from app.workflows.spec import Gate, Step
    from app.workflows.spec_loader import spec_from_dict

    _asset(db_session)
    ctx = db_session.query(RequestModel).filter_by(id=_report(db_session)["request_id"]).one().state_context

    notify, task = spec_from_dict(_load_catalog()["data_issue_report"]).stages
    assert isinstance(notify, Step) and notify.tool.name == "send_notification"
    args = notify.args(ctx)
    assert args["subject"] == "Data issue reported: main.sales.orders"
    assert args["body"] == ctx["_email_body"] and "Row counts dropped" in args["body"]
    assert args["to_email"] is None  # the governance group gets it unless an admin names someone
    assert isinstance(task, Gate) and task.type == "manual_task" and task.due_in_days == 5

    shown = _get_workflow_params(ctx)
    assert set(shown) == {"asset", "asset_type", "owner", "summary", "details"}
