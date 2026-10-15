"""The duplicate-request gate in execute_workflow, and the check_duplicate_requests tool.

Only agent submissions pass through execute_workflow; form/API submissions and
sandboxed workflow-test runs must be unaffected by the gate.
"""
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.db.event import EventModel
from app.db.request import RequestModel
from app.models.request import RequestCreate
from app.services.request_service import RequestService
from app.tools.execute_workflow import execute_workflow
from app.tools.self_service.check_duplicate_requests import check_duplicate_requests
from app.tools.tool_executor import ToolContext, ToolExecutor
from tests.factories.request_factory import RequestFactory

ME = "me@example.com"
OTHER = "other@example.com"
WF = "workspace_provision"
PARAMS = {
    "business_domain": "supply_chain", "environment": "prod", "cost_center": "CC-1",
    "data_sensitivity": "green", "admin_group": "scm-admins", "justification": "New domain",
    "workspace_name": "ws-supply_chain-prod", "region": "eastus2",
}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "DUPLICATE_CHECK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "DUPLICATE_CHECK_LOOKBACK_DAYS", 14, raising=False)
    monkeypatch.setattr(settings, "DUPLICATE_CHECK_STATUSES", ["pending", "completed"], raising=False)


@pytest.fixture
def db(db_session):
    with patch("app.tools.execute_workflow.get_db", side_effect=lambda: iter([db_session])), \
         patch("app.tools.self_service.check_duplicate_requests.get_lakebase_session", return_value=db_session):
        yield db_session


def _existing(db, requester=OTHER, **ctx):
    return RequestFactory.create(db, type=WF, status="pending", requester_email=requester,
                                 state_context={**PARAMS, **ctx})


def _count(db, wf=WF):
    return db.query(RequestModel).filter(RequestModel.type == wf).count()


def _facts(db, request_id, fact_type):
    return db.query(EventModel).filter(
        EventModel.request_id == request_id, EventModel.event_type == fact_type
    ).all()


class TestGate:
    @pytest.mark.asyncio
    async def test_blocks_without_acknowledgement(self, db):
        _existing(db, justification="Different wording")
        out = await execute_workflow.execute(workflow_type=WF, parameters=PARAMS, _user_email=ME)
        assert out["submitted"] is False and "success" not in out
        assert out["status"] == "possible_duplicates"
        [match] = out["possible_duplicates"]
        assert match["requester"] == "another user" and "request_id" not in match
        assert "acknowledge_duplicates=true" in out["message"]
        assert _count(db) == 1

    @pytest.mark.asyncio
    async def test_proceeds_with_acknowledgement_and_records_it(self, db):
        _existing(db)
        out = await execute_workflow.execute(
            workflow_type=WF, parameters=PARAMS, acknowledge_duplicates=True, _user_email=ME
        )
        assert out["success"] is True
        assert _count(db) == 2
        [fact] = _facts(db, out["request_id"], "possible_duplicates_acknowledged")
        [match] = fact.event_data["matches"]
        assert match["request_id"] is None and match["submitted_by_you"] is False
        assert fact.event_data["actor"] == ME

    @pytest.mark.asyncio
    async def test_own_duplicate_is_reported_with_its_link(self, db):
        mine = _existing(db, requester=ME)
        out = await execute_workflow.execute(workflow_type=WF, parameters=PARAMS, _user_email=ME)
        assert out["possible_duplicates"][0]["request_link"] == f"[{mine.id}](/requests/{mine.id})"

    @pytest.mark.asyncio
    async def test_disabled_setting_bypasses_the_gate(self, db, monkeypatch):
        monkeypatch.setattr(settings, "DUPLICATE_CHECK_ENABLED", False)
        _existing(db)
        out = await execute_workflow.execute(workflow_type=WF, parameters=PARAMS, _user_email=ME)
        assert out["success"] is True
        assert _count(db) == 2
        assert _facts(db, out["request_id"], "possible_duplicates_acknowledged") == []

    @pytest.mark.asyncio
    async def test_no_duplicates_submits_normally(self, db):
        _existing(db, business_domain="finance", workspace_name="ws-finance-prod")
        out = await execute_workflow.execute(workflow_type=WF, parameters=PARAMS, _user_email=ME)
        assert out["success"] is True
        assert _facts(db, out["request_id"], "request_submitted")
        assert _facts(db, out["request_id"], "possible_duplicates_acknowledged") == []

    @pytest.mark.asyncio
    async def test_check_failure_fails_open(self, db):
        _existing(db)
        with patch("app.services.duplicate_requests.find_duplicate_requests", side_effect=RuntimeError("boom")):
            out = await execute_workflow.execute(workflow_type=WF, parameters=PARAMS, _user_email=ME)
        assert out["success"] is True

    @pytest.mark.asyncio
    async def test_agent_sees_the_matches_through_the_executor(self, db, monkeypatch):
        """The hold is a pause, not a failure: the executor passes it through
        unwrapped, so chat and the audit don't record an error."""
        monkeypatch.setattr(settings, "AGENT_TOOL_OPA_ENFORCE", False, raising=False)
        executor = ToolExecutor()

        async def _allow(t, args, c):
            return {"allow": True, "requires_approval": False, "approval_type": "", "reason": "ok"}
        monkeypatch.setattr(executor, "_evaluate_policy", _allow)

        _existing(db)
        ctx = ToolContext(tool_call_id="tc-1", user_identity={"email": ME, "roles": "User"})
        out = await executor.run(execute_workflow, ctx, workflow_type=WF, parameters=PARAMS)
        assert "tool_failed" not in out and "error" not in out
        assert out["status"] == "possible_duplicates"
        assert len(out["possible_duplicates"]) == 1
        assert _count(db) == 1


class TestOtherCallersUnaffected:
    def test_form_and_api_submissions_skip_the_check(self, db):
        _existing(db)
        with patch("app.services.duplicate_requests.find_duplicate_requests") as find:
            req = RequestService.create_request(
                db, RequestCreate(type=WF, title="Form request", requester_email=ME, metadata=dict(PARAMS))
            )
        assert req.id and _count(db) == 2
        find.assert_not_called()

    @pytest.mark.asyncio
    async def test_sandboxed_test_runs_never_reach_the_gate(self, db, monkeypatch):
        monkeypatch.setattr(settings, "AGENT_TOOL_OPA_ENFORCE", False, raising=False)
        executor = ToolExecutor()

        async def _allow(t, args, c):
            return {"allow": True, "requires_approval": False, "approval_type": "", "reason": "ok"}
        monkeypatch.setattr(executor, "_evaluate_policy", _allow)

        _existing(db)
        ctx = ToolContext(tool_call_id="dr-1", user_identity={"email": ME}, dry_run=True)
        with patch("app.services.duplicate_requests.find_duplicate_requests") as find:
            out = await executor.run(execute_workflow, ctx, workflow_type=WF, parameters=PARAMS)
        assert out["status"] == "simulated"
        find.assert_not_called()
        assert _count(db) == 1


class TestCheckTool:
    @pytest.mark.asyncio
    async def test_reports_matches(self, db):
        mine = _existing(db, requester=ME)
        out = await check_duplicate_requests.execute(workflow_type=WF, parameters=PARAMS, _user_email=ME)
        assert out["enabled"] is True and out["count"] == 1
        assert out["possible_duplicates"][0]["request_id"] == mine.id
        assert "business_domain" in out["compared_fields"]

    @pytest.mark.asyncio
    async def test_disabled(self, db, monkeypatch):
        monkeypatch.setattr(settings, "DUPLICATE_CHECK_ENABLED", False)
        _existing(db)
        out = await check_duplicate_requests.execute(workflow_type=WF, parameters=PARAMS, _user_email=ME)
        assert out == {
            "enabled": False, "checked": False, "count": 0, "possible_duplicates": [],
            "message": "Duplicate checking is turned off in this environment.",
        }

    @pytest.mark.asyncio
    async def test_errors_are_reported_not_hidden(self, db):
        with patch("app.services.duplicate_requests.find_duplicate_requests", side_effect=RuntimeError("db down")):
            out = await check_duplicate_requests.execute(workflow_type=WF, parameters=PARAMS, _user_email=ME)
        assert out["checked"] is False and "db down" in out["error"]

    def test_is_a_read_tool(self):
        assert check_duplicate_requests.side_effect_class == "read"
        assert not check_duplicate_requests.is_mutating
