"""The rbac_access_change workflow: spec, step tools and approver routing (ADR 0005 D4, D6, D8)."""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.workflows.tools as T
from app.core.exceptions import PermanentError
from app.services.rbac import approvers
from app.services.rbac import requests as rbac_requests
from app.tools.tool_executor import ToolContext, ToolExecutor
from app.workflows.graphs.specs import SPECS, editable_states
from app.workflows.instructions import execution_contract
from app.workflows.spec import build_spec_graph
from app.workflows.spec_loader import SpecError, lint_step_tool_args, spec_from_dict
from app.workflows.tool_registry import available_tools, get_tool, has_tool

KEY = "rbac_access_change"
ITEMS = [{"action": "grant", "environment": "dev", "securable_type": "schema", "catalog": "sales_dev",
          "schema_name": "orders", "principal_name": "g_new", "privileges": ["SELECT"]}]
JUSTIFICATION = "Needed to build the quarterly revenue dashboard for finance"
_ALLOW = {"allow": True, "requires_approval": False, "approval_type": "none", "reason": "test"}
_ARGS = {
    "items": {"$var": {"path": "items", "default": None}},
    "justification": {"$var": {"path": "justification", "default": ""}},
    "group_ref": {"$var": {"path": "group_ref", "default": None}},
}
# The spec as first shipped: requests filed then are checkpointed under these node names,
# and an admin copy may still name these tools and route with approvers_from.
OLD_SPEC = {
    "key": KEY, "name": KEY, "complete_fact": "rbac_change_recorded",
    "stages": [
        {"kind": "step", "name": "plan", "tool": "rbac_plan_access_change",
         "writes_context": ["approvers", "nothing_to_change"], "args": _ARGS},
        {"kind": "gate", "name": "approve", "type": "platform_admin", "waiting_status": "manager_approval",
         "editable": False, "auto_approve": {"$var": {"path": "nothing_to_change", "default": False}},
         "approvers_from": {"$var": "approvers"}},
        {"kind": "step", "name": "apply", "tool": "apply_access_change", "success_fact": "rbac_ledger_updated",
         "args": {}},
    ],
}


def test_spec_loads_with_prepare_gate_record():
    spec = SPECS[KEY]
    compiled = spec_from_dict(spec)
    assert [s.name for s in compiled.stages] == ["prepare", "approve", "record"]
    assert [s.renamed_from for s in compiled.stages] == [["plan"], [], ["apply"]]
    assert lint_step_tool_args(spec) == []
    # Only the access-change inputs are offered to the agent; a direct edit is filed by the Access Rules page.
    assert execution_contract(spec, request_type=KEY)["parameters"] == ["items", "justification", "group_ref"]
    gate = spec["stages"][1]
    assert gate["type"] == "platform_admin" and gate["approver"] == {"source": "access_rule_type"}
    assert "approvers_from" not in gate
    assert spec["complete_fact"] != spec["stages"][2]["success_fact"]


def test_the_gate_offers_no_edit_and_restart():
    # Editing the prepared change before approving it would sidestep the two-person rule.
    assert SPECS[KEY]["stages"][1]["editable"] is False
    assert editable_states(KEY) == []
    with pytest.raises(SpecError, match="editable must be true or false"):
        spec_from_dict({**SPECS[KEY], "stages": [
            SPECS[KEY]["stages"][0], {**SPECS[KEY]["stages"][1], "editable": "no"}, SPECS[KEY]["stages"][2]]})


def test_step_tools_are_registered_with_their_side_effect_classes():
    prepare, record = get_tool("rbac_prepare_access_change"), get_tool("record_access_change")
    assert prepare is T.rbac_prepare_access_change and record is T.record_access_change
    assert (prepare.side_effect_class, prepare.is_mutating) == ("read", False)
    assert (record.side_effect_class, record.is_mutating) == ("data_grant", True)


def test_the_former_tool_names_resolve_but_are_not_offered_to_authors():
    assert get_tool("rbac_plan_access_change") is T.rbac_prepare_access_change
    assert get_tool("apply_access_change") is T.record_access_change
    assert has_tool("rbac_plan_access_change") and has_tool("apply_access_change")
    offered = {t["name"] for t in available_tools()}
    assert {"rbac_prepare_access_change", "record_access_change"} <= offered
    assert not offered & {"rbac_plan_access_change", "apply_access_change"}


@pytest.mark.asyncio
async def test_step_tools_run_through_the_tool_executor_with_the_request_id(monkeypatch):
    seen = {}

    def prepare_step(request_id, **kwargs):
        seen["prepare"] = (request_id, kwargs)
        return {"plane": "uc", "report_markdown": "prepared"}

    def record_step(request_id):
        seen["record"] = request_id
        return {"change_set_id": "cs-1", "message": "Updated 1 ledger row(s)."}

    monkeypatch.setattr(rbac_requests, "prepare_step", prepare_step)
    monkeypatch.setattr(rbac_requests, "record_step", record_step)
    executor = ToolExecutor()
    monkeypatch.setattr(executor, "_evaluate_policy", AsyncMock(return_value=dict(_ALLOW)))
    ctx = ToolContext(scope_id="req-7", user_identity={"email": "ana@example.com"})

    prepared = await executor.run(T.rbac_prepare_access_change, ctx, items=ITEMS, justification=JUSTIFICATION,
                                  group_ref="grp-1")
    recorded = await executor.run(T.record_access_change, ctx)

    assert prepared["plane"] == "uc"
    assert seen["prepare"] == ("req-7", {"items": ITEMS, "justification": JUSTIFICATION, "group_ref": "grp-1"})
    assert recorded["change_set_id"] == "cs-1" and seen["record"] == "req-7"


@pytest.mark.asyncio
async def test_a_permanent_error_from_the_prepare_step_reaches_the_caller(monkeypatch):
    def prepare_step(request_id, **kwargs):
        raise PermanentError("Split by type")

    monkeypatch.setattr(rbac_requests, "prepare_step", prepare_step)
    with pytest.raises(PermanentError, match="Split by type"):
        await ToolExecutor().run(T.rbac_prepare_access_change, ToolContext(scope_id="req-8"), items=ITEMS)


GROUPS = {"uc": "uc-approvers", "workspace": "ws-approvers"}
START = {"request_id": "req-1", "status": "pending", "context": {
    "items": ITEMS, "justification": JUSTIFICATION, "requested_by_email": "ana@example.com"}}
CONFIG = {"configurable": {"thread_id": "req-1"}}


def _prepared(plane="uc", nothing_to_change=False):
    """What prepare_step returns; ``approvers`` is still returned for specs that route with approvers_from."""
    group = GROUPS.get(plane)
    return {"plane": plane, "approver_group": group, "approvers": [group] if group else [],
            "nothing_to_change": nothing_to_change, "report_markdown": "prepared"}


@contextmanager
def _stubbed(monkeypatch, groups=GROUPS):
    """Stub the ledger services, the approver-group settings and the DB the step nodes open."""
    monkeypatch.setattr(approvers, "approver_group", lambda plane: groups.get(plane))
    with patch("app.db.session.get_db", side_effect=lambda: iter([MagicMock()])), \
            patch("app.state_machines.facts.add_fact"), \
            patch("app.state_machines.facts.get_latest_fact", return_value=None), \
            patch.object(ToolExecutor, "_evaluate_policy", AsyncMock(return_value=dict(_ALLOW))):
        yield


def _compile(spec, saver):
    return build_spec_graph(spec_from_dict(spec)).compile(checkpointer=saver)


async def _waiting(compiled):
    state = await compiled.aget_state(CONFIG)
    return [i.value for t in state.tasks for i in (t.interrupts or ())]


async def _run_graph(monkeypatch, prepare_result, groups=GROUPS):
    """Run the catalog graph with the step services stubbed; returns (gate payloads, record calls, final state)."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    recorded = []
    monkeypatch.setattr(rbac_requests, "prepare_step", lambda request_id, **kw: dict(prepare_result))
    monkeypatch.setattr(rbac_requests, "record_step",
                        lambda request_id: recorded.append(request_id) or {"change_set_id": "cs-1", "message": "ok"})
    compiled = _compile(SPECS[KEY], MemorySaver())
    payloads = []
    with _stubbed(monkeypatch, groups):
        await compiled.ainvoke(START, CONFIG)
        for _ in range(3):
            waiting = await _waiting(compiled)
            if not waiting:
                break
            payloads.extend(waiting)
            await compiled.ainvoke(Command(resume={"approved": True}), CONFIG)
        state = await compiled.aget_state(CONFIG)
    return payloads, recorded, state.values


@pytest.mark.asyncio
@pytest.mark.parametrize("plane, group", [("uc", "uc-approvers"), ("workspace", "ws-approvers")])
async def test_the_approval_goes_to_the_types_group(monkeypatch, plane, group):
    payloads, recorded, state = await _run_graph(monkeypatch, _prepared(plane))
    [gate] = payloads
    assert (gate["type"], gate["gate"], gate["approvers"]) == ("platform_admin", "approve", [group])
    assert recorded == ["req-1"] and state["status"] == "completed"


@pytest.mark.asyncio
async def test_without_a_group_the_approval_is_left_to_platform_admins(monkeypatch):
    payloads, recorded, _ = await _run_graph(monkeypatch, _prepared("uc_metastore"))
    [gate] = payloads
    # No assignee: the poller files an unassigned platform_admin approval, which only Platform Admins act on.
    assert "approvers" not in gate and gate["type"] == "platform_admin"
    assert recorded == ["req-1"]


@pytest.mark.asyncio
async def test_nothing_to_change_skips_the_approval(monkeypatch):
    payloads, recorded, state = await _run_graph(monkeypatch, _prepared(nothing_to_change=True))
    assert payloads == [] and recorded == ["req-1"] and state["status"] == "completed"


@pytest.mark.asyncio
async def test_an_admin_copy_with_the_former_names_still_routes_to_the_group(monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver

    monkeypatch.setattr(rbac_requests, "prepare_step", lambda request_id, **kw: _prepared("workspace"))
    old = _compile(OLD_SPEC, MemorySaver())
    with _stubbed(monkeypatch):
        await old.ainvoke(START, CONFIG)
        [gate] = await _waiting(old)
    assert (gate["gate"], gate["approvers"]) == ("approve", ["ws-approvers"])


@pytest.mark.asyncio
async def test_a_request_waiting_for_approval_before_the_rename_resumes_into_record(monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    calls = []
    monkeypatch.setattr(rbac_requests, "prepare_step",
                        lambda request_id, **kw: calls.append("prepare") or _prepared())
    monkeypatch.setattr(rbac_requests, "record_step",
                        lambda request_id: calls.append("record") or {"change_set_id": "cs-1", "message": "ok"})
    saver = MemorySaver()
    with _stubbed(monkeypatch):
        old = _compile(OLD_SPEC, saver)
        await old.ainvoke(START, CONFIG)
        assert (await old.aget_state(CONFIG)).next == ("approve",)

        new = _compile(SPECS[KEY], saver)
        await new.ainvoke(Command(resume={"approved": True}), CONFIG)
        state = await new.aget_state(CONFIG)
    assert calls == ["prepare", "record"]
    assert state.next == () and state.values["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failing, stalled_in", [("prepare_step", "plan"), ("record_step", "apply")])
async def test_a_request_stalled_in_a_renamed_step_finishes_after_the_rename(monkeypatch, failing, stalled_in):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    calls = []

    def flaky(name, result):
        def step(request_id, **kw):
            if name == failing and name not in calls:
                calls.append(name)
                raise RuntimeError("the ledger is unreachable")
            calls.append(name)
            return result
        return step

    monkeypatch.setattr(rbac_requests, "prepare_step", flaky("prepare_step", _prepared()))
    monkeypatch.setattr(rbac_requests, "record_step", flaky("record_step", {"change_set_id": "cs-1", "message": "ok"}))
    saver = MemorySaver()
    with _stubbed(monkeypatch):
        old = _compile(OLD_SPEC, saver)
        try:
            await old.ainvoke(START, CONFIG)
            await old.ainvoke(Command(resume={"approved": True}), CONFIG)
        except Exception:  # noqa: BLE001 - the step's failure is what leaves the checkpoint in it
            pass
        assert (await old.aget_state(CONFIG)).next == (stalled_in,)

        new = _compile(SPECS[KEY], saver)
        await new.ainvoke(None, CONFIG)
        if await _waiting(new):
            await new.ainvoke(Command(resume={"approved": True}), CONFIG)
        state = await new.aget_state(CONFIG)
    assert state.next == () and state.values["status"] == "completed"
    assert calls.count("record_step") == (2 if failing == "record_step" else 1)
