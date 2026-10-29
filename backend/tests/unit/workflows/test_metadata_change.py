"""The metadata_change workflow: spec, gates, step tools, filing, prepare and record (ADR-0006 D8).

The change service is stubbed here (it has its own tests); the request row, facts and
prepared state are real, in a private in-memory database.
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db  # noqa: F401  (registers every model on Base.metadata)
import app.workflows.tools as T
from app.core.exceptions import PermanentError
from app.db import RequestModel
from app.db.base import Base
from app.services.metadata import authz, changes
from app.services.metadata import requests as rq
from app.services.metadata.errors import VersionConflict
from app.services.metadata.types import (
    Actor,
    AuthzDecision,
    ChangeItem,
    ChangePreview,
    ChangeRequest,
    ChangeSet,
    ItemDecision,
    VersionConflictItem,
)
from app.state_machines.facts import add_fact, get_latest_fact
from app.tools.tool_executor import ToolContext, ToolExecutor
from app.workflows.graphs.specs import SPECS
from app.workflows.instructions import execution_contract, with_canonical_execution
from app.workflows.spec import build_spec_graph
from app.workflows.spec_loader import lint_step_tool_args, spec_from_dict
from app.workflows.tool_registry import get_tool

KEY = "metadata_change"
_ALLOW = {"allow": True, "requires_approval": False, "approval_type": "none", "reason": "test"}
OWNER = Actor(id="ana@example.com", email="ana@example.com")
PIPELINE = Actor(id="app-1", actor_type="service_principal")


def _item(key="pii", after=None, *, column="email", version=0, name="sales_dev.crm.customers") -> ChangeItem:
    return ChangeItem(fqn_key=f"{name}::{column}" if column else name, name=name, column=column,
                      securable_type="column" if column else "table", environment="dev", field="tag", key=key,
                      action="set", after=after, before_version=version)


def _decision(item, *, sensitive=()) -> ItemDecision:
    return ItemDecision(item=item, decision=AuthzDecision(needs_approval=True, can_request=True,
                                                          sensitive_reasons=list(sensitive),
                                                          approver_groups=["crm-owners"]))


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# --- Spec and tools ---------------------------------------------------------------------


def test_spec_loads_with_prepare_two_gates_and_record():
    spec = SPECS[KEY]
    compiled = spec_from_dict(spec)
    assert [s.name for s in compiled.stages] == ["prepare", "owner_approval", "governance_approval", "record"]
    assert lint_step_tool_args(spec) == []
    owner, governance = spec["stages"][1], spec["stages"][2]
    assert owner["approver"] == {"source": "metadata_owner", "role": "owner"}
    assert governance["approver"] == {"source": "metadata_owner", "role": "governance"}
    assert owner["editable"] is False and governance["editable"] is False


@pytest.mark.parametrize("ctx, owner_skips, governance_skips", [
    ({"nothing_to_change": True}, True, True),
    ({"nothing_to_change": False, "sensitive": False}, False, True),
    ({"nothing_to_change": False, "sensitive": True}, False, False),
    # A prepare result without the flag fails closed: the governance gate runs.
    ({"nothing_to_change": False}, False, False),
])
def test_the_gates_skip_only_when_nothing_changes_or_nothing_is_sensitive(ctx, owner_skips, governance_skips):
    stages = spec_from_dict(SPECS[KEY]).stages
    assert bool(stages[1].auto_approve(ctx)) is owner_skips
    assert bool(stages[2].auto_approve(ctx)) is governance_skips


def test_step_tools_are_registered_with_their_side_effect_classes():
    prepare, record = get_tool("prepare_metadata_change"), get_tool("record_metadata_change")
    assert prepare is T.prepare_metadata_change and record is T.record_metadata_change
    assert (prepare.side_effect_class, prepare.is_mutating) == ("read", False)
    assert (record.side_effect_class, record.is_mutating) == ("app_write", True)


def test_the_served_instructions_point_at_the_submit_tool_not_execute_workflow():
    contract = execution_contract(SPECS[KEY], request_type=KEY)
    served = with_canonical_execution("# Metadata\n\n## Execution\nCall `execute_workflow`.", SPECS[KEY],
                                      request_type=KEY)
    assert contract["filed_by"] == "submit_metadata_change"
    assert "Call `submit_metadata_change`" in served
    assert "Call `execute_workflow`" not in served


def test_execute_workflow_refuses_a_type_filed_by_another_tool(monkeypatch):
    from app.services.workflow_service import WorkflowService
    from app.tools.execute_workflow import execute_workflow

    monkeypatch.setattr(WorkflowService, "is_known_request_type", staticmethod(lambda db, t: True))
    with patch("app.tools.execute_workflow.get_db", side_effect=lambda: iter([MagicMock()])), \
            patch("app.workflows.graphs.published_graph_spec", return_value=None):
        out = execute_workflow._func(workflow_type=KEY, parameters={"ops": []}, _user_email="ana@example.com")
    assert out["success"] is False
    assert "submit_metadata_change" in out["error"]


@pytest.mark.asyncio
async def test_step_tools_run_through_the_tool_executor_with_the_request_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(rq, "prepare_step", lambda request_id: seen.setdefault("prepare", request_id) and {
        "nothing_to_change": False, "sensitive": False, "approver_groups": ["crm-owners"]})
    monkeypatch.setattr(rq, "record_step", lambda request_id: seen.setdefault("record", request_id) and {
        "change_set_id": "cs-1"})
    executor = ToolExecutor()
    monkeypatch.setattr(executor, "_evaluate_policy", AsyncMock(return_value=dict(_ALLOW)))
    ctx = ToolContext(scope_id="req-7", user_identity={"email": "ana@example.com"})

    prepared = await executor.run(T.prepare_metadata_change, ctx)
    recorded = await executor.run(T.record_metadata_change, ctx)

    assert prepared["approver_groups"] == ["crm-owners"] and seen["prepare"] == "req-7"
    assert recorded["change_set_id"] == "cs-1" and seen["record"] == "req-7"


# --- Graph routing ----------------------------------------------------------------------------

START = {"request_id": "req-1", "status": "pending", "context": {"requested_by_email": "ana@example.com"}}
CONFIG = {"configurable": {"thread_id": "req-1"}}


@contextmanager
def _stubbed():
    with patch("app.db.session.get_db", side_effect=lambda: iter([MagicMock()])), \
            patch("app.state_machines.facts.add_fact"), \
            patch("app.state_machines.facts.get_latest_fact", return_value=None), \
            patch.object(ToolExecutor, "_evaluate_policy", AsyncMock(return_value=dict(_ALLOW))):
        yield


async def _run_graph(monkeypatch, prepare_result, governance_group="data-governance"):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from app.core.config import settings

    recorded = []
    monkeypatch.setattr(settings, "METADATA_GOVERNANCE_APPROVER_GROUP", governance_group)
    monkeypatch.setattr(rq, "prepare_step", lambda request_id: dict(prepare_result))
    monkeypatch.setattr(rq, "record_step", lambda request_id: recorded.append(request_id) or {"change_set_id": "cs"})
    monkeypatch.setattr(rq, "owner_approver_groups_for", lambda request_id: ["crm-owners"])
    compiled = build_spec_graph(spec_from_dict(SPECS[KEY])).compile(checkpointer=MemorySaver())
    payloads = []
    with _stubbed():
        await compiled.ainvoke(START, CONFIG)
        for _ in range(4):
            state = await compiled.aget_state(CONFIG)
            waiting = [i.value for t in state.tasks for i in (t.interrupts or ())]
            if not waiting:
                break
            payloads.extend(waiting)
            await compiled.ainvoke(Command(resume={"approved": True}), CONFIG)
        state = await compiled.aget_state(CONFIG)
    return payloads, recorded, state.values


@pytest.mark.asyncio
async def test_a_plain_change_waits_for_the_owner_groups_only(monkeypatch):
    payloads, recorded, state = await _run_graph(monkeypatch, {"nothing_to_change": False, "sensitive": False})
    [gate] = payloads
    assert (gate["gate"], gate["approvers"]) == ("owner_approval", ["crm-owners"])
    assert recorded == ["req-1"] and state["status"] == "completed"


@pytest.mark.asyncio
async def test_a_sensitive_change_also_waits_for_governance(monkeypatch):
    payloads, recorded, _ = await _run_graph(monkeypatch, {"nothing_to_change": False, "sensitive": True})
    assert [(p["gate"], p.get("approvers")) for p in payloads] == [
        ("owner_approval", ["crm-owners"]), ("governance_approval", ["data-governance"])]
    assert recorded == ["req-1"]


@pytest.mark.asyncio
async def test_nothing_to_change_skips_both_approvals(monkeypatch):
    payloads, recorded, state = await _run_graph(monkeypatch, {"nothing_to_change": True, "sensitive": True})
    assert payloads == [] and recorded == ["req-1"] and state["status"] == "completed"


# --- Filing, prepare and record ---------------------------------------------------------------


def _file(db, items, actor=OWNER, reason="Flag the email column as PII"):
    decisions = [_decision(i) for i in items]
    return rq.file_change_request(db, request=ChangeRequest(ops=rq.ops_for_items(items), reason=reason),
                                  preview=ChangePreview(items=decisions), items=decisions, actor=actor, source="ui")


def test_ops_for_items_pin_each_tag_to_the_version_it_was_resolved_against():
    ops = rq.ops_for_items([_item("pii", None, version=0), _item("classification", "confidential", version=3)])
    [op] = ops
    assert op.op == "set_tags" and op.column == "email"
    assert op.tags == {"pii": None, "classification": "confidential"}
    assert op.expected_versions == {"pii": 0, "classification": 3}


def test_filing_records_who_filed_it_and_from_where(db):
    request_id = _file(db, [_item()], actor=PIPELINE)

    row = db.get(RequestModel, request_id)
    assert row.type == KEY and row.requester_email == "app-1"
    assert row.state_context["source"] == "ui"
    assert row.title == "Metadata change: set pii on sales_dev.crm.customers.email"
    filed = get_latest_fact(db, request_id, rq.FILED_FACT).event_data
    assert (filed["actor"], filed["actor_type"]) == ("app-1", "service_principal")
    assert get_latest_fact(db, request_id, "request_submitted") is not None


@pytest.fixture
def previewed(monkeypatch):
    seen = {}

    def preview(db, ask, actor):
        seen.update(ask=ask, actor=actor)
        return seen.get("result") or ChangePreview(items=[_decision(_item(), sensitive=["Removes the PII flag."])],
                                                   approval_count=1, digest="d-1", report_markdown="**report**")

    def preview_revert(db, change_set_id, request, actor):
        seen.update(reverts=change_set_id, actor=actor)
        return preview(db, None, actor)

    monkeypatch.setattr(changes, "preview", preview)
    monkeypatch.setattr(changes, "preview_revert", preview_revert)
    monkeypatch.setattr(authz, "owner_groups", lambda db, key, **kw: (["crm-owners"], "sales_dev.crm.customers"))
    return seen


def test_prepare_stores_the_items_owner_groups_and_sensitivity(db, previewed):
    request_id = _file(db, [_item()])

    out = rq.prepare(db, request_id)

    assert out == {"nothing_to_change": False, "sensitive": True, "approver_groups": ["crm-owners"],
                   "change_count": 1, "report_title": "Metadata change", "report_markdown": "**report**"}
    prepared = db.get(RequestModel, request_id).state_context[rq.PREPARED_CONTEXT_KEY]
    assert prepared["approver_groups"] == ["crm-owners"]
    assert prepared["sensitive_reasons"] == ["Removes the PII flag."]
    assert rq.owner_approver_groups(db, request_id) == ["crm-owners"]
    assert previewed["actor"].id == OWNER.id


def test_prepare_carries_a_reverts_change_set_id_from_the_filing_record(db, previewed):
    decisions = [_decision(_item())]
    request_id = rq.file_change_request(
        db, request=ChangeRequest(ops=rq.ops_for_items([_item()]), reason="Undo"),
        preview=ChangePreview(items=decisions), items=decisions, actor=OWNER, source="revert",
        reverts_change_set_id="cs-1",
    )

    rq.prepare(db, request_id)

    assert previewed["reverts"] == "cs-1"
    prepared = db.get(RequestModel, request_id).state_context[rq.PREPARED_CONTEXT_KEY]
    assert prepared["reverts_change_set_id"] == "cs-1"
    assert "reverts_change_set_id" not in db.get(RequestModel, request_id).state_context


def test_prepare_takes_the_principal_type_from_the_filing_record_not_context(db, previewed):
    request_id = _file(db, [_item()], actor=PIPELINE)
    row = db.get(RequestModel, request_id)
    row.state_context = {**row.state_context, "actor_type": "user"}
    db.commit()

    rq.prepare(db, request_id)

    assert previewed["actor"].actor_type == "service_principal"


def test_prepare_refuses_a_request_not_filed_through_the_change_service(db, previewed):
    db.add(RequestModel(id="req-x", type=KEY, title="x", status="pending", requester_email="ana@example.com",
                        state_context={"ops": [{"op": "set_tags", "name": "a.b.c", "tags": {"pii": None}}]}))
    db.commit()

    with pytest.raises(PermanentError, match="wasn't submitted through the Metadata Manager"):
        rq.prepare(db, "req-x")


def test_prepare_refuses_ops_edited_after_filing(db, previewed):
    request_id = _file(db, [_item()])
    row = db.get(RequestModel, request_id)
    row.state_context = {**row.state_context, "ops": [{"op": "set_tags", "name": "sales_prd.crm.customers",
                                                       "tags": {"pii": None}}]}
    db.commit()

    with pytest.raises(PermanentError, match="edited after it was filed"):
        rq.prepare(db, request_id)


def test_prepare_refuses_a_blocked_change(db, previewed):
    from app.services.metadata.types import Finding

    previewed["result"] = ChangePreview(blocked=True, findings=[
        Finding(code="key_unknown", severity="deny", message="'colour' isn't a known key.")])
    request_id = _file(db, [_item()])

    with pytest.raises(PermanentError, match="'colour' isn't a known key."):
        rq.prepare(db, request_id)


@pytest.fixture
def recorded(monkeypatch):
    calls = []

    def record_prepared(db, *, request_id, prepared, requester, approver):
        calls.append({"request_id": request_id, "items": prepared["items"], "requester": requester,
                      "approver": approver})
        return ChangeSet(id="cs-1", actor=requester.id, source="ui", request_id=request_id)

    monkeypatch.setattr(changes, "record_prepared", record_prepared)
    return calls


def test_record_writes_exactly_the_prepared_items_with_the_approver(db, previewed, recorded):
    request_id = _file(db, [_item()])
    rq.prepare(db, request_id)
    add_fact(db, request_id, "approval_received", {"approved_by": "lead@example.com"}, actor="lead@example.com")

    out = rq.record(db, request_id)

    assert out["change_set_id"] == "cs-1" and out["already_recorded"] is False
    [call] = recorded
    assert call["approver"] == "lead@example.com"
    assert [i["key"] for i in call["items"]] == ["pii"]


def test_record_refuses_a_prepared_change_edited_after_preparing(db, previewed, recorded):
    request_id = _file(db, [_item()])
    rq.prepare(db, request_id)
    row = db.get(RequestModel, request_id)
    ctx = dict(row.state_context)
    ctx[rq.PREPARED_CONTEXT_KEY] = {**ctx[rq.PREPARED_CONTEXT_KEY], "items": []}
    row.state_context = ctx
    db.commit()

    with pytest.raises(PermanentError, match="edited after it was prepared"):
        rq.record(db, request_id)
    assert recorded == []


def test_record_turns_a_version_conflict_into_a_permanent_error(db, previewed, monkeypatch):
    def record_prepared(*a, **k):
        raise VersionConflict([VersionConflictItem(fqn_key="x", name="x", key="pii", expected_version=0,
                                                   current_version=1)])

    monkeypatch.setattr(changes, "record_prepared", record_prepared)
    request_id = _file(db, [_item()])
    rq.prepare(db, request_id)

    with pytest.raises(PermanentError, match="wasn't recorded; file it again"):
        rq.record(db, request_id)
