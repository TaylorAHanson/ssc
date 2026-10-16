"""The rbac_access_change workflow: spec, step tools and approver routing (ADR 0005 D4, D6, D8)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.workflows.tools as T
from app.core.exceptions import PermanentError
from app.services.rbac import requests as rbac_requests
from app.tools.tool_executor import ToolContext, ToolExecutor
from app.workflows.graphs.specs import SPECS, editable_states
from app.workflows.instructions import execution_contract
from app.workflows.spec import build_spec_graph
from app.workflows.spec_loader import SpecError, lint_step_tool_args, spec_from_dict
from app.workflows.tool_registry import get_tool

KEY = "rbac_access_change"
ITEMS = [{"action": "grant", "environment": "dev", "securable_type": "schema", "catalog": "sales_dev",
          "schema_name": "orders", "principal_name": "g_new", "privileges": ["SELECT"]}]
JUSTIFICATION = "Needed to build the quarterly revenue dashboard for finance"
_ALLOW = {"allow": True, "requires_approval": False, "approval_type": "none", "reason": "test"}


def test_spec_loads_with_plan_gate_apply():
    spec = SPECS[KEY]
    compiled = spec_from_dict(spec)
    assert [s.name for s in compiled.stages] == ["plan", "approve", "apply"]
    assert lint_step_tool_args(spec) == []
    # Only the access-change inputs are offered to the agent; a direct edit is filed by the Access Rules page.
    assert execution_contract(spec, request_type=KEY)["parameters"] == ["items", "justification", "group_ref"]
    gate = spec["stages"][1]
    assert gate["approvers_from"] == {"$var": "approvers"}
    assert spec["complete_fact"] != spec["stages"][2]["success_fact"]


def test_the_gate_offers_no_edit_and_restart():
    # Editing the stored plan before approving it would sidestep the two-person rule.
    assert SPECS[KEY]["stages"][1]["editable"] is False
    assert editable_states(KEY) == []
    with pytest.raises(SpecError, match="editable must be true or false"):
        spec_from_dict({**SPECS[KEY], "stages": [
            SPECS[KEY]["stages"][0], {**SPECS[KEY]["stages"][1], "editable": "no"}, SPECS[KEY]["stages"][2]]})


def test_step_tools_are_registered_with_their_side_effect_classes():
    plan, apply = get_tool("rbac_plan_access_change"), get_tool("apply_access_change")
    assert plan is T.rbac_plan_access_change and apply is T.apply_access_change
    assert (plan.side_effect_class, plan.is_mutating) == ("read", False)
    assert (apply.side_effect_class, apply.is_mutating) == ("data_grant", True)


@pytest.mark.asyncio
async def test_step_tools_run_through_the_tool_executor_with_the_request_id(monkeypatch):
    seen = {}

    def plan_step(request_id, **kwargs):
        seen["plan"] = (request_id, kwargs)
        return {"approvers": ["uc-approvers"], "report_markdown": "plan"}

    def apply_step(request_id):
        seen["apply"] = request_id
        return {"change_set_id": "cs-1", "message": "Updated 1 ledger row(s)."}

    monkeypatch.setattr(rbac_requests, "plan_step", plan_step)
    monkeypatch.setattr(rbac_requests, "apply_step", apply_step)
    executor = ToolExecutor()
    monkeypatch.setattr(executor, "_evaluate_policy", AsyncMock(return_value=dict(_ALLOW)))
    ctx = ToolContext(scope_id="req-7", user_identity={"email": "ana@example.com"})

    planned = await executor.run(T.rbac_plan_access_change, ctx, items=ITEMS, justification=JUSTIFICATION,
                                 group_ref="grp-1")
    applied = await executor.run(T.apply_access_change, ctx)

    assert planned["approvers"] == ["uc-approvers"]
    assert seen["plan"] == ("req-7", {"items": ITEMS, "justification": JUSTIFICATION, "group_ref": "grp-1"})
    assert applied["change_set_id"] == "cs-1" and seen["apply"] == "req-7"


@pytest.mark.asyncio
async def test_a_permanent_error_from_the_plan_step_reaches_the_caller(monkeypatch):
    def plan_step(request_id, **kwargs):
        raise PermanentError("Split by type")

    monkeypatch.setattr(rbac_requests, "plan_step", plan_step)
    with pytest.raises(PermanentError, match="Split by type"):
        await ToolExecutor().run(T.rbac_plan_access_change, ToolContext(scope_id="req-8"), items=ITEMS)


async def _run_graph(monkeypatch, plan_result):
    """Run the catalog graph with the step services stubbed; returns (gate payloads, apply calls, final state)."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    applied = []
    monkeypatch.setattr(rbac_requests, "plan_step", lambda request_id, **kw: dict(plan_result))
    monkeypatch.setattr(rbac_requests, "apply_step",
                        lambda request_id: applied.append(request_id) or {"change_set_id": "cs-1", "message": "ok"})
    compiled = build_spec_graph(spec_from_dict(SPECS[KEY])).compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "req-1"}}
    payloads = []

    async def pending():
        state = await compiled.aget_state(config)
        return [i.value for t in state.tasks for i in (t.interrupts or ())]

    with patch("app.db.session.get_db", side_effect=lambda: iter([MagicMock()])), \
            patch("app.state_machines.facts.add_fact"), \
            patch("app.state_machines.facts.get_latest_fact", return_value=None), \
            patch.object(ToolExecutor, "_evaluate_policy", AsyncMock(return_value=dict(_ALLOW))):
        await compiled.ainvoke({"request_id": "req-1", "status": "pending", "context": {
            "items": ITEMS, "justification": JUSTIFICATION, "requested_by_email": "ana@example.com"}}, config)
        for _ in range(3):
            waiting = await pending()
            if not waiting:
                break
            payloads.extend(waiting)
            await compiled.ainvoke(Command(resume={"approved": True}), config)
        state = await compiled.aget_state(config)
    return payloads, applied, state.values


@pytest.mark.asyncio
async def test_the_approval_goes_to_the_types_group(monkeypatch):
    payloads, applied, state = await _run_graph(
        monkeypatch, {"approvers": ["uc-approvers"], "nothing_to_change": False, "report_markdown": "plan"})
    [gate] = payloads
    assert (gate["type"], gate["gate"], gate["approvers"]) == ("platform_admin", "approve", ["uc-approvers"])
    assert applied == ["req-1"] and state["status"] == "completed"


@pytest.mark.asyncio
async def test_without_a_group_the_approval_is_left_to_platform_admins(monkeypatch):
    payloads, applied, _ = await _run_graph(
        monkeypatch, {"approvers": [], "nothing_to_change": False, "report_markdown": "plan"})
    [gate] = payloads
    # No assignee: the poller files an unassigned platform_admin approval, which only Platform Admins act on.
    assert "approvers" not in gate and gate["type"] == "platform_admin"
    assert applied == ["req-1"]


@pytest.mark.asyncio
async def test_nothing_to_change_skips_the_approval(monkeypatch):
    payloads, applied, state = await _run_graph(
        monkeypatch, {"approvers": ["uc-approvers"], "nothing_to_change": True, "report_markdown": "plan"})
    assert payloads == [] and applied == ["req-1"] and state["status"] == "completed"
