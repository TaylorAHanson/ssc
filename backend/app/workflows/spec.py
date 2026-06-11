"""
Declarative workflow specs -> LangGraph graphs.

The V2 "workflows as data" thesis: a workflow is a linear sequence of *stages* —
human approval gates and provision steps — so we describe them as data
(:class:`WorkflowSpec`) and compile them to a durable LangGraph graph with one
generic builder. Every registered request type is expressed this way (the data
catalog lives in ``graphs/specs.py``); there is no dedicated code-graph path.

Stage ordering is preserved exactly, so a plan-between-gates flow like
``[Gate(manager), Step(plan), Gate(platform_admin, reviews plan), Step(apply)]``
expresses naturally.

Gates are native LangGraph ``interrupt()`` points. Every gate resumes with a
dict ``{"approved": bool, "reason": str}`` regardless of gate kind (human
approval, training completion, PR merge, children done) — the executor maps the
underlying fact/event to that shape.

Provision steps run their tool through the shared ``ToolExecutor`` so they are
OPA-gated, idempotent (per request + step), and audited.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TypedDict, Union

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

logger = logging.getLogger(__name__)


class WorkflowState(TypedDict, total=False):
    request_id: str
    context: Dict[str, Any]
    status: str
    results: Dict[str, Any]
    gates: Dict[str, bool]
    rejected: bool
    rejection_reason: Optional[str]


@dataclass
class Gate:
    """A human/event approval gate, compiled to an ``interrupt()`` node."""
    name: str                 # node id, e.g. "manager_approval"
    type: str                 # "manager" | "platform_admin" | "data_owner" | "training" | "pr_merge" | "children"
    waiting_status: str = "manager_approval"   # RequestStatus while paused
    # Skip the gate entirely when this predicate of context is true (auto-approve).
    auto_approve: Optional[Callable[[Dict[str, Any]], bool]] = None
    # Optional expression of context that resolves the gate's approver(s) at
    # runtime — e.g. data-owner groups discovered by a prior resolve step. When
    # set, the resolved value is surfaced in the interrupt payload (as both
    # ``data_owners`` and ``approvers``) so the approval layer can route to the
    # right people. This is what lets a data_owner gate be fully data-defined.
    approvers_from: Optional[Callable[[Dict[str, Any]], Any]] = None
    # Declarative approver source — the no-code way to point ANY gate at its
    # approver(s) without wiring a separate resolve step. One of:
    #   "group"              -> a hardcoded group/role name (``approver_group``)
    #   "approver_group_tag" -> resolve the UC ``approver_group`` tag off the
    #                           request's assets (``approver_assets_from``),
    #                           optionally falling back to the asset owner.
    # The gate node resolves this to a list of approver identifiers and surfaces
    # them in the interrupt payload (``approvers``/``data_owners``). Takes
    # precedence over ``approvers_from`` when set.
    approver_source: Optional[str] = None
    approver_group: Optional[str] = None
    approver_assets_from: Optional[Callable[[Dict[str, Any]], Any]] = None
    approver_fallback_to_owner: bool = True


@dataclass
class Step:
    """A provision step that runs ``tool`` (optionally once per item)."""
    name: str
    tool: Any                                   # McpTool
    args: Callable[[Dict[str, Any]], Dict[str, Any]] = lambda ctx: {}
    running_status: str = "provisioning"
    approvals: List[str] = field(default_factory=list)  # passed to ToolContext gate
    for_each: Optional[Callable[[Dict[str, Any]], List[Any]]] = None
    item_args: Optional[Callable[[Dict[str, Any], Any], Dict[str, Any]]] = None
    # Optional fact to write on success (UI parity, e.g. "access_granted").
    success_fact: Optional[str] = None
    # Optional predicate of context: when present and false, the step is SKIPPED
    # (its tool never runs and no success_fact is written). Enables conditional
    # branching — e.g. only run an extra approval/notification step for certain
    # request shapes. When None, the step always runs.
    run_if: Optional[Callable[[Dict[str, Any]], bool]] = None
    # Keys to lift from this step's (single, non-fan-out) tool result into the
    # graph ``context`` so later stages can read them — e.g. a resolve_owners
    # step writing ``data_owners`` for a downstream gate's ``approvers_from``.
    writes_context: Optional[List[str]] = None


Stage = Union[Gate, Step]


@dataclass
class WorkflowSpec:
    name: str
    stages: List[Stage] = field(default_factory=list)
    completed_status: str = "completed"
    complete_fact: Optional[str] = None


# --------------------------------------------------------------------------
# Node factories
# --------------------------------------------------------------------------
def _gate_node(gate: Gate):
    async def node(state: WorkflowState) -> WorkflowState:
        ctx = state.get("context", {})
        gates = dict(state.get("gates", {}))
        if gate.auto_approve and gate.auto_approve(ctx):
            gates[gate.name] = True
            logger.info("[%s] gate '%s' auto-approved", state["request_id"], gate.name)
            return {"gates": gates, "status": gate.waiting_status}
        payload: Dict[str, Any] = {
            "type": gate.type,
            "gate": gate.name,
            "request_id": state["request_id"],
        }
        approvers = await _resolve_gate_approvers(gate, ctx)
        if approvers:
            # Surface under both keys: ``data_owners`` keeps parity with the old
            # dedicated data-access graph; ``approvers`` is the generic name.
            payload["data_owners"] = approvers
            payload["approvers"] = approvers
        decision = interrupt(payload)
        approved, reason = _decode_decision(decision)
        gates[gate.name] = approved
        out: WorkflowState = {"gates": gates}
        if not approved:
            out["rejected"] = True
            out["rejection_reason"] = reason
        return out

    return node


async def _resolve_gate_approvers(gate: "Gate", ctx: Dict[str, Any]) -> List[str]:
    """Resolve a gate's approver identifier(s) to a flat list of strings.

    Precedence: the declarative ``approver_source`` (``group`` literal or
    ``approver_group_tag`` UC-tag lookup) wins; otherwise fall back to the
    ``approvers_from`` expression for backward compatibility. Group/role names
    and owner emails are both returned as-is — the approval layer decides how to
    route each (email -> assignee, otherwise -> role/group).
    """
    if gate.approver_source == "group":
        return [gate.approver_group] if gate.approver_group else []
    if gate.approver_source == "approver_group_tag":
        # Lazy import keeps the pure graph module free of provider/IO imports.
        from app.workflows.tools import resolve_owner_groups_from_assets

        assets = (
            gate.approver_assets_from(ctx)
            if gate.approver_assets_from is not None
            else ctx.get("assets")
        )
        return await resolve_owner_groups_from_assets(
            assets, fallback_to_owner=gate.approver_fallback_to_owner
        )
    if gate.approvers_from is not None:
        val = gate.approvers_from(ctx)
        if val is None:
            return []
        return list(val) if isinstance(val, (list, tuple)) else [val]
    return []


def _decode_decision(decision: Any):
    if isinstance(decision, dict):
        approved = bool(decision.get("approved", True)) if "approved" in decision else True
        return approved, decision.get("reason")
    return bool(decision), None


def _step_node(step: Step):
    async def node(state: WorkflowState) -> WorkflowState:
        from app.db.session import get_db
        from app.state_machines.facts import add_fact
        from app.tools.tool_executor import ToolContext, executor

        ctx = state.get("context", {})
        request_id = state["request_id"]
        principal = ctx.get("requested_by_email")
        results = dict(state.get("results", {}))
        step_results: List[Any] = []

        # Conditional branching: skip the step entirely when its run_if predicate
        # is false. The graph edge to the next node is unconditional, so flow
        # continues; we record a skip marker (no tool run, no success_fact).
        if step.run_if is not None and not step.run_if(ctx):
            logger.info("[%s] step '%s' skipped (run_if=false)", request_id, step.name)
            results[step.name] = {"skipped": True}
            return {"results": results, "status": step.running_status}

        items = step.for_each(ctx) if step.for_each else [None]
        db = next(get_db())
        try:
            for idx, item in enumerate(items):
                kwargs = (
                    step.item_args(ctx, item)
                    if (item is not None and step.item_args)
                    else step.args(ctx)
                )
                tool_ctx = ToolContext(
                    tool_call_id=f"{step.name}:{idx}",
                    user_identity={"email": principal},
                    db=db,
                    scope_id=request_id,
                    approvals=list(step.approvals),
                )
                res = await executor.run(step.tool, tool_ctx, **kwargs)
                step_results.append(res)
            results[step.name] = step_results
            if step.success_fact:
                add_fact(db, request_id, step.success_fact,
                         {"step": step.name, "results": step_results}, actor="system")
        finally:
            db.close()
        out: WorkflowState = {"results": results, "status": step.running_status}
        # Propagate selected result keys into context for downstream stages
        # (e.g. resolved data_owners feeding a gate's approvers_from).
        if step.writes_context and len(step_results) == 1 and isinstance(step_results[0], dict):
            res0 = step_results[0]
            new_ctx = dict(ctx)
            for key in step.writes_context:
                if key in res0:
                    new_ctx[key] = res0[key]
            out["context"] = new_ctx
        return out

    return node


def _complete_node(spec: WorkflowSpec):
    async def node(state: WorkflowState) -> WorkflowState:
        if spec.complete_fact:
            from app.db.session import get_db
            from app.state_machines.facts import add_fact
            db = next(get_db())
            try:
                add_fact(db, state["request_id"], spec.complete_fact, {}, actor="system")
            finally:
                db.close()
        return {"status": spec.completed_status}

    return node


async def _rejected_node(state: WorkflowState) -> WorkflowState:
    from app.db.session import get_db
    from app.state_machines.facts import add_fact
    db = next(get_db())
    try:
        add_fact(db, state["request_id"], "request_rejected",
                 {"reason": state.get("rejection_reason")}, actor="approver")
    finally:
        db.close()
    return {"status": "rejected"}


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------
def build_spec_graph(spec: WorkflowSpec) -> StateGraph:
    """Compile a :class:`WorkflowSpec` into an (uncompiled) StateGraph."""
    g = StateGraph(WorkflowState)
    node_ids: List[str] = []

    for stage in spec.stages:
        if isinstance(stage, Gate):
            g.add_node(stage.name, _gate_node(stage))
        else:
            g.add_node(stage.name, _step_node(stage))
        node_ids.append(stage.name)

    g.add_node("complete", _complete_node(spec))
    g.add_node("rejected", _rejected_node)

    # Sequential wiring. Gates branch to "rejected" when not approved.
    entry = node_ids[0] if node_ids else "complete"
    g.add_edge(START, entry)

    for i, stage in enumerate(spec.stages):
        nxt = node_ids[i + 1] if i + 1 < len(node_ids) else "complete"
        if isinstance(stage, Gate):
            g.add_conditional_edges(
                stage.name,
                _gate_router(stage.name, nxt),
                {"next": nxt, "rejected": "rejected"},
            )
        else:
            g.add_edge(stage.name, nxt)

    g.add_edge("complete", END)
    g.add_edge("rejected", END)
    return g


def _gate_router(gate_name: str, next_node: str):
    def router(state: WorkflowState) -> str:
        return "next" if state.get("gates", {}).get(gate_name) else "rejected"

    return router
