"""
Declarative workflow specs -> LangGraph graphs.

The V2 "workflows as data" thesis: most workflows are a linear sequence of
*stages* — human approval gates and provision steps — so we describe them as
data (:class:`WorkflowSpec`) and compile them to a durable LangGraph graph with
one generic builder. Specialized pipelines (enforcement, reporting, job runs,
orchestrators) get dedicated builders in ``graphs/special.py``.

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
        decision = interrupt(
            {
                "type": gate.type,
                "gate": gate.name,
                "request_id": state["request_id"],
            }
        )
        approved, reason = _decode_decision(decision)
        gates[gate.name] = approved
        out: WorkflowState = {"gates": gates}
        if not approved:
            out["rejected"] = True
            out["rejection_reason"] = reason
        return out

    return node


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
        return {"results": results, "status": step.running_status}

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
