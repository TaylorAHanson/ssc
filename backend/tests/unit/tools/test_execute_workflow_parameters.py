"""execute_workflow refuses parameters whose names the workflow's steps don't read."""
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.db.request import RequestModel
from app.tools.execute_workflow import _parameter_name_mismatch, execute_workflow
from app.workflows.graphs.specs import SPECS

ME = "me@example.com"
WF = "workspace_access"
FULL = {"workspace": "ws-dev", "access_group": "analysts", "scope": "user", "is_auto_approve": False}


@pytest.fixture(autouse=True)
def _no_duplicate_check(monkeypatch):
    monkeypatch.setattr(settings, "DUPLICATE_CHECK_ENABLED", False, raising=False)


@pytest.fixture
def db(db_session):
    with patch("app.tools.execute_workflow.get_db", side_effect=lambda: iter([db_session])):
        yield db_session


def _count(db):
    return db.query(RequestModel).filter(RequestModel.type == WF).count()


@pytest.mark.asyncio
async def test_renamed_inputs_are_refused_before_a_request_is_filed(db):
    out = await execute_workflow.execute(
        workflow_type=WF,
        parameters={"workspace_name": "ws-dev", "group": "analysts", "scope": "user", "is_auto_approve": False},
        _user_email=ME,
    )
    assert out["success"] is False
    assert "Missing: access_group, workspace" in out["error"]
    assert "Not recognised: group, workspace_name" in out["error"]
    assert _count(db) == 0


@pytest.mark.asyncio
async def test_expected_names_are_filed(db):
    out = await execute_workflow.execute(workflow_type=WF, parameters=FULL, _user_email=ME)
    assert out["success"] is True
    assert _count(db) == 1


def test_an_input_left_out_passes():
    params = {k: v for k, v in FULL.items() if k != "is_auto_approve"}
    assert _parameter_name_mismatch(SPECS[WF], params) is None


def test_an_extra_name_with_nothing_missing_passes():
    assert _parameter_name_mismatch(SPECS[WF], {**FULL, "notes": "x"}) is None


def test_platform_injected_names_are_not_unrecognised():
    params = {"workspace": "ws-dev", "access_group": "analysts", "requested_by_email": ME}
    assert _parameter_name_mismatch(SPECS[WF], params) is None


def test_compound_workflows_are_not_checked():
    spec = {"stages": [{"kind": "subworkflow", "name": "child", "ref": WF}, *SPECS[WF]["stages"]]}
    assert _parameter_name_mismatch(spec, {"workspace_name": "ws-dev"}) is None


def test_no_spec_is_not_checked():
    assert _parameter_name_mismatch(None, {"anything": 1}) is None
