"""Tests for what happens when a gate says no.

Every gate's failure edge lands on one built-in terminal node, so that node is the
only place a denial can be acted on. Two things must hold: the requester is told
(nothing downstream can tell them — the node ends the graph), and a compound
workflow tells them once rather than once per nesting level.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workflows.spec import _rejected_node, build_spec_graph
from app.workflows.spec_loader import SpecError, spec_from_dict, validate_spec_dict

STATE = {"request_id": "req-1", "rejection_reason": "Use the shared workspace instead."}


async def _run(nested: bool):
    """Run the rejection node with its IO stubbed, returning (state, notifier)."""
    notifier = AsyncMock(return_value={"sent": True})
    with patch("app.db.session.get_db", return_value=iter([MagicMock()])), \
            patch("app.state_machines.facts.add_fact") as add_fact, \
            patch("app.services.rejection_notice.notify_requester_of_rejection", notifier):
        out = await _rejected_node(nested=nested)(dict(STATE))
    return out, notifier, add_fact


@pytest.mark.asyncio
async def test_rejection_notifies_the_requester_with_the_reason():
    out, notifier, add_fact = await _run(nested=False)
    assert out == {"status": "rejected"}
    assert notifier.await_count == 1
    # The reason has to travel with it, or the notice can only say "denied".
    assert notifier.await_args.args[2] == "Use the shared workspace instead."
    assert add_fact.call_count == 1


@pytest.mark.asyncio
async def test_a_nested_workflow_does_not_send_its_own_notice():
    """A child's rejection routes to the child's rejected node AND the parent's.
    Notifying from both would email the requester twice for one decision."""
    out, notifier, _ = await _run(nested=True)
    assert out == {"status": "rejected"}
    assert notifier.await_count == 0


@pytest.mark.asyncio
async def test_a_broken_notifier_still_rejects_the_request():
    """The decision is already made. If the notice could raise out of this node,
    the executor would record a DENIED request as a FAILED one."""
    with patch("app.db.session.get_db", return_value=iter([MagicMock()])), \
            patch("app.state_machines.facts.add_fact") as add_fact, \
            patch("app.services.rejection_notice.notify_requester_of_rejection",
                  AsyncMock(side_effect=RuntimeError("provider exploded"))):
        out = await _rejected_node(nested=False)(dict(STATE))
    assert out == {"status": "rejected"}
    assert add_fact.call_count == 1


# --- authored rejection steps (``on_reject``) -----------------------------
#
# The platform notice covers "somebody was told". These cover the other half the
# authoring assistant had no way to express: a workflow doing its OWN thing when a
# gate says no — a tailored message, closing a ticket, releasing a placeholder.

def _spec_with_on_reject(**overrides):
    step = {
        "kind": "step", "name": "tell_them", "tool": "send_notification",
        "args": {"to_email": {"$var": "requested_by_email"},
                 "subject": {"$literal": "Denied"},
                 "body": {"$var": "rejection_reason"}},
    }
    step.update(overrides)
    return {
        "name": "wf", "complete_fact": "done",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager"},
            {"kind": "step", "name": "grant", "tool": "add_group_membership",
             "args": {"group": {"$literal": "g"}, "members": {"$list": ["a"]}}},
        ],
        "on_reject": [step],
    }


def test_on_reject_steps_compile_and_sit_ahead_of_the_terminal_node():
    spec = spec_from_dict(_spec_with_on_reject())
    (rejection_step,) = spec.on_reject
    assert rejection_step.tool.name == "send_notification"
    graph = build_spec_graph(spec)
    assert {"tell_them", "rejected"} <= set(graph.nodes)


def test_on_reject_steps_attest_no_approvals():
    """A regular step inherits the gates before it because the graph proves they
    passed. Here one demonstrably did NOT, so attesting it would lie to the policy
    layer about a mutating tool running on an unapproved request."""
    spec = spec_from_dict(_spec_with_on_reject())
    assert spec.stages[1].approvals == ["manager"]   # the happy path still inherits
    assert spec.on_reject[0].approvals == []


def test_claiming_an_approval_on_the_rejection_path_is_rejected():
    spec = _spec_with_on_reject(approvals=["manager"])
    with pytest.raises(SpecError, match="DENIED"):
        validate_spec_dict(spec)


def test_a_gate_cannot_live_on_the_rejection_path():
    """Nothing is left to approve once the request has been denied."""
    spec = _spec_with_on_reject()
    spec["on_reject"] = [{"kind": "gate", "name": "another_look", "type": "manager"}]
    with pytest.raises(SpecError, match="must be 'step'"):
        validate_spec_dict(spec)


def test_a_rejection_step_cannot_reuse_a_stage_name():
    """Both write into the same ``results`` map, so a shared name loses one."""
    spec = _spec_with_on_reject(name="grant")
    with pytest.raises(SpecError, match="duplicate"):
        validate_spec_dict(spec)


def test_a_typo_on_the_rejection_path_still_fails_loudly():
    """The same unknown-key protection as ``stages`` — a dropped ``run_if`` would
    silently turn a conditional rejection step into an unconditional one."""
    spec = _spec_with_on_reject(when={"$var": "x"})
    with pytest.raises(SpecError, match="run_if"):
        validate_spec_dict(spec)


@pytest.mark.asyncio
async def test_a_failing_rejection_step_does_not_bury_the_denial():
    """If this raised, the executor would record a DENIED request as FAILED and the
    poller would retry it — the requester's request would look broken instead of
    answered."""
    from app.workflows.spec import _reject_step_node

    step = spec_from_dict(_spec_with_on_reject()).on_reject[0]
    with patch("app.workflows.spec._step_node",
               return_value=AsyncMock(side_effect=RuntimeError("smtp down"))):
        node = _reject_step_node(step)
    out = await node({"request_id": "req-1", "results": {}})
    assert out["results"]["tell_them"]["error"] == "smtp down"


@pytest.mark.asyncio
async def test_a_denied_gate_runs_the_authored_step_and_hands_it_the_reason():
    """The whole chain, on a real graph: the gate denies, the authored step runs
    with the approver's reason available to its args, and the request still ends
    rejected. The reason reaching the step is the part that makes an authored
    rejection notice worth anything."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    calls = []

    async def fake_run(tool, tool_ctx, **kwargs):
        calls.append({"tool": getattr(tool, "name", "?"), "kwargs": kwargs,
                      "approvals": list(tool_ctx.approvals)})
        return {"sent": True}

    graph = build_spec_graph(spec_from_dict(_spec_with_on_reject()))
    compiled = graph.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "req-1"}}
    notifier = AsyncMock(return_value={"sent": True})

    with patch("app.db.session.get_db", side_effect=lambda: iter([MagicMock()])), \
            patch("app.state_machines.facts.add_fact"), \
            patch("app.tools.tool_executor.executor.run", side_effect=fake_run), \
            patch("app.tools.tool_executor.is_tool_failure", return_value=None), \
            patch("app.services.rejection_notice.notify_requester_of_rejection", notifier):
        await compiled.ainvoke(
            {"request_id": "req-1", "context": {"requested_by_email": "r@corp.com"},
             "status": "pending"},
            config,
        )
        await compiled.ainvoke(
            Command(resume={"approved": False, "reason": "Use the shared workspace."}),
            config,
        )
        state = await compiled.aget_state(config)

    assert [c["tool"] for c in calls] == ["send_notification"], "the grant must not run"
    assert calls[0]["kwargs"]["body"] == "Use the shared workspace."
    assert calls[0]["kwargs"]["to_email"] == "r@corp.com"
    assert calls[0]["approvals"] == []
    assert state.values["status"] == "rejected"
    assert notifier.await_count == 1


def test_every_gate_routes_to_the_terminal_rejection_node():
    """The wiring this all depends on: a gate has exactly one failure target."""
    spec = spec_from_dict({
        "name": "wf", "complete_fact": "done",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager"},
            {"kind": "step", "name": "notify", "tool": "send_notification",
             "args": {"to_email": {"$literal": "x@y"}, "subject": {"$literal": "s"},
                      "body": {"$literal": "b"}}},
        ],
    })
    graph = build_spec_graph(spec)
    assert "rejected" in graph.nodes
