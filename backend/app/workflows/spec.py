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
    # "manager" | "platform_admin" | "data_owner" | "training" | "pr_merge"
    # | "manual_task" | "children"
    type: str
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
    # For ``type == "training"`` gates: the specific LMS course this gate requires.
    # When set, the gate is auto-satisfied once the requester has a completion for
    # ``course_code`` (looked up via the training provider), instead of requiring a
    # manual "mark complete". ``course_name`` is display-only copy for the UI.
    course_code: Optional[str] = None
    course_name: Optional[str] = None
    # For ``type == "manual_task"`` gates: what the assignee must actually DO
    # off-platform before marking the task done (there is no tool for this work —
    # that's the point of the gate). Required by the loader, carried in the
    # interrupt payload, and shown verbatim in the approvals inbox.
    instructions: Optional[str] = None
    # Optional SLA in days, used for aging/escalation visibility. A manual task can
    # park a request indefinitely, so the inbox needs to be able to show "overdue".
    due_in_days: Optional[int] = None


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


@dataclass
class SubWorkflow:
    """A nested workflow composed into this one as a subgraph (compound workflows).

    The referenced workflow (``ref`` = a known workflow key) is compiled and added
    as a LangGraph subgraph node that shares this graph's :class:`WorkflowState`.
    Its gates interrupt and resume through the same poller path as native gates
    (they're sequential), and a rejection inside the child rejects the parent.

    ``input`` optionally maps parent context -> additional child context (merged,
    additively, into the shared ``context`` before the subgraph runs). Because the
    context is shared, results the child writes via its steps' ``writes_context``
    are already visible to later parent stages; ``writes_context`` here is an
    explicit, forward-compatible declaration of which of those keys this stage
    contributes.
    """
    name: str
    ref: str
    input: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    writes_context: Optional[List[str]] = None
    running_status: str = "provisioning"
    # Conditional composition: when set and false for a request, the whole nested
    # workflow is SKIPPED (its gates/steps never run) and flow continues to the
    # next stage — the building block for "do they need a git repo? if yes …".
    run_if: Optional[Callable[[Dict[str, Any]], bool]] = None


Stage = Union[Gate, Step, SubWorkflow]


@dataclass
class WorkflowSpec:
    name: str
    stages: List[Stage] = field(default_factory=list)
    completed_status: str = "completed"
    complete_fact: Optional[str] = None
    # Steps to run when a gate denies the request, before the terminal rejection
    # node. This is the authorable half of "what happens on reject" — the platform
    # already emails the requester a default notice (see
    # ``app/services/rejection_notice.py``); these are for anything workflow-
    # specific: a tailored message, closing a ticket, releasing a placeholder.
    #
    # Two properties they do NOT share with ``stages``:
    #   * They attest NO approvals. A regular step inherits the gates before it
    #     because the graph proves those gates passed; here one demonstrably did
    #     not, so a mutating tool is judged by OPA with nothing attested.
    #   * A failure is logged and skipped rather than raised. The request was
    #     denied, and a broken cleanup step must not re-file that as a failure.
    on_reject: List[Step] = field(default_factory=list)


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
        # Training gates may pin a specific LMS course so the poller can
        # auto-satisfy the gate from the requester's completions and the UI can
        # tell them exactly what to finish.
        if gate.type == "training" and gate.course_code:
            payload["course_code"] = gate.course_code
            if gate.course_name:
                payload["course_name"] = gate.course_name
        # Manual tasks carry their own instructions into the inbox: the assignee is
        # being asked to do work the platform can't do, so the task text IS the
        # entire content of the item they see.
        if gate.type == "manual_task":
            payload["instructions"] = gate.instructions or ""
            if gate.due_in_days:
                payload["due_in_days"] = gate.due_in_days
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
            # Also mirror the decision into ``context``, because that is what
            # authored expressions read: without it an ``on_reject`` step can't
            # quote the reason in a message or branch on which gate said no.
            out["context"] = {
                **ctx, "rejection_reason": reason, "rejected_gate": gate.name,
            }
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
        from app.tools.tool_executor import ToolContext, executor, is_tool_failure

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
                # Halt the graph on a (real or false) tool failure so we never
                # write the success_fact or advance to the next stage. The
                # executor already converts false-successes into an error-shaped
                # dict, so a plain envelope re-check (no predicate) catches both
                # that and any policy refusal / out-of-scope dict it returned.
                failure_reason = is_tool_failure(res)
                if failure_reason:
                    tool_name = getattr(step.tool, "name", "?")
                    raise RuntimeError(
                        f"step '{step.name}' tool '{tool_name}' failed: {failure_reason}"
                    )
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


# Non-terminal status a *nested* (subworkflow) graph emits when it finishes,
# instead of leaking its terminal "completed" into the shared parent state. If a
# child set ``status="completed"`` and the parent then paused on a gate (whose
# interrupt runs before any status write), the executor would persist the parent
# request as COMPLETED and the poller would never resume it (stranded approval).
_NESTED_COMPLETE_STATUS = "provisioning"


def _complete_node(spec: WorkflowSpec, *, nested: bool = False):
    async def node(state: WorkflowState) -> WorkflowState:
        if spec.complete_fact:
            from app.db.session import get_db
            from app.state_machines.facts import add_fact
            db = next(get_db())
            try:
                add_fact(db, state["request_id"], spec.complete_fact, {}, actor="system")
            finally:
                db.close()
        # Only the TOP-LEVEL graph may declare the request terminally complete.
        # A nested child returns a neutral running status so the parent's
        # remaining stages (which may pause on a gate) are reached with the
        # request still in-progress, not falsely COMPLETED.
        if nested:
            return {"status": _NESTED_COMPLETE_STATUS}
        return {"status": spec.completed_status}

    return node


def _reject_step_node(step: Step):
    """An ``on_reject`` step: a normal step whose failure can't derail the denial.

    On the happy path a failing step must halt the graph — the next stage would act
    on work that didn't happen. Here the decision is already final, so a failure is
    recorded in ``results`` and flow continues to the terminal rejection node.
    Otherwise a broken cleanup step would leave a DENIED request marked FAILED, and
    the poller would keep retrying it.
    """
    inner = _step_node(step)

    async def node(state: WorkflowState) -> WorkflowState:
        try:
            return await inner(state)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "[%s] on_reject step '%s' failed (the rejection still stands): %s",
                state.get("request_id"), step.name, e, exc_info=True,
            )
            results = dict(state.get("results", {}))
            results[step.name] = {"error": str(e)}
            return {"results": results}

    return node


def _rejected_node(*, nested: bool = False):
    """The terminal rejection path: record the fact, then close the loop.

    ``nested`` mirrors :func:`_complete_node`. A child's rejection routes to the
    child's rejected node AND then to the parent's, so only the top-level graph
    notifies — otherwise a compound workflow emails the requester once per level.
    """
    async def node(state: WorkflowState) -> WorkflowState:
        from app.db.session import get_db
        from app.state_machines.facts import add_fact
        db = next(get_db())
        try:
            add_fact(db, state["request_id"], "request_rejected",
                     {"reason": state.get("rejection_reason")}, actor="approver")
            if not nested:
                # Nothing downstream of here can tell the requester — this node
                # ends the graph — so the notice is sent from inside it. Guarded:
                # the request WAS denied, and letting a notification problem raise
                # here would record that decision as a graph failure instead.
                try:
                    from app.services.rejection_notice import notify_requester_of_rejection

                    await notify_requester_of_rejection(
                        db, state["request_id"], state.get("rejection_reason")
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "[%s] rejection notice failed: %s", state["request_id"], e, exc_info=True
                    )
        finally:
            db.close()
        return {"status": "rejected"}

    return node


def _subworkflow_input_node(sub: "SubWorkflow"):
    """Pre-node that merges a subworkflow's mapped ``input`` into shared context."""
    async def node(state: WorkflowState) -> WorkflowState:
        ctx = dict(state.get("context", {}))
        mapped = sub.input(ctx) if sub.input else {}
        if isinstance(mapped, dict) and mapped:
            ctx.update(mapped)
        return {"context": ctx, "status": sub.running_status}

    return node


async def _subworkflow_guard_node(state: WorkflowState) -> WorkflowState:
    """No-op gate node for a conditional subworkflow; the run/skip decision is on
    its outgoing edge so the nested graph is entered only when ``run_if`` holds."""
    return {}


def _subworkflow_skip_router(sub: "SubWorkflow", run_target: str, skip_target: str):
    """Route a conditional subworkflow to its body or past it based on ``run_if``."""
    def router(state: WorkflowState) -> str:
        ctx = state.get("context", {})
        return run_target if bool(sub.run_if(ctx)) else skip_target

    return router


# Limits guarding against pathological / malicious nesting from authored specs.
_MAX_SUBWORKFLOW_DEPTH = 5


def _subworkflow_entry_id(stage: "SubWorkflow") -> str:
    """The first node id of a subworkflow stage's sequence (guard → input → body)."""
    if stage.run_if is not None:
        return f"{stage.name}__guard"
    if stage.input is not None:
        return f"{stage.name}__input"
    return stage.name


def build_spec_graph(
    spec: WorkflowSpec,
    child_resolver: Optional[Callable[[str], "WorkflowSpec"]] = None,
    *,
    _depth: int = 0,
    _seen: Optional[frozenset] = None,
) -> StateGraph:
    """Compile a :class:`WorkflowSpec` into an (uncompiled) StateGraph.

    ``child_resolver`` resolves a workflow key referenced by a :class:`SubWorkflow`
    stage to its :class:`WorkflowSpec`. It is threaded from the graph registry so
    this module stays IO-free (no DB/catalog imports). When a spec has no
    subworkflow stages, ``child_resolver`` is unused.
    """
    from app.workflows.spec_loader import SpecError

    _seen = _seen or frozenset()
    g = StateGraph(WorkflowState)

    # The id that begins each stage's node sequence — needed up front so a stage's
    # outgoing edge can target the *entry* of the next stage (a subworkflow may
    # front its body with a guard/input node).
    def entry_of(stage) -> str:
        return _subworkflow_entry_id(stage) if isinstance(stage, SubWorkflow) else stage.name

    entries = [entry_of(s) for s in spec.stages]

    def next_entry(i: int) -> str:
        return entries[i + 1] if i + 1 < len(entries) else "complete"

    g.add_node("complete", _complete_node(spec, nested=_depth > 0))
    g.add_node("rejected", _rejected_node(nested=_depth > 0))

    # The rejection path: authored ``on_reject`` steps chained ahead of the terminal
    # node, so every gate's failure edge points at the first of them instead of
    # straight at the end of the graph.
    reject_entry = "rejected"
    if spec.on_reject:
        for idx, step in enumerate(spec.on_reject):
            g.add_node(step.name, _reject_step_node(step))
            nxt_reject = (
                spec.on_reject[idx + 1].name if idx + 1 < len(spec.on_reject) else "rejected"
            )
            g.add_edge(step.name, nxt_reject)
        reject_entry = spec.on_reject[0].name

    for i, stage in enumerate(spec.stages):
        nxt = next_entry(i)
        if isinstance(stage, Gate):
            g.add_node(stage.name, _gate_node(stage))
            g.add_conditional_edges(
                stage.name,
                _gate_router(stage.name, nxt),
                {"next": nxt, "rejected": reject_entry},
            )
        elif isinstance(stage, SubWorkflow):
            if _depth + 1 > _MAX_SUBWORKFLOW_DEPTH:
                raise SpecError(
                    f"subworkflow nesting exceeds max depth {_MAX_SUBWORKFLOW_DEPTH} "
                    f"(at stage '{stage.name}' -> '{stage.ref}')"
                )
            if child_resolver is None:
                raise SpecError(
                    f"stage '{stage.name}' references workflow '{stage.ref}' but no "
                    "child_resolver was provided to compile it"
                )
            if stage.ref in _seen:
                raise SpecError(
                    f"subworkflow cycle detected: '{stage.ref}' is already on the "
                    "composition path"
                )
            child_spec = child_resolver(stage.ref)
            if child_spec is None:
                raise SpecError(
                    f"stage '{stage.name}' references unknown workflow '{stage.ref}'"
                )
            child_graph = build_spec_graph(
                child_spec, child_resolver,
                _depth=_depth + 1, _seen=_seen | {stage.ref},
            )
            # A compiled subgraph shares the parent's WorkflowState + checkpointer
            # (LangGraph namespaces the child's internal nodes under this node id,
            # so nested gate/step names can't collide with the parent's).
            g.add_node(stage.name, child_graph.compile())
            # Body chain: [guard?] -> [input?] -> subgraph. The subgraph's exit
            # branches to rejected (child rejected) or the next stage.
            body_target = f"{stage.name}__input" if stage.input is not None else stage.name
            if stage.input is not None:
                g.add_node(f"{stage.name}__input", _subworkflow_input_node(stage))
                g.add_edge(f"{stage.name}__input", stage.name)
            if stage.run_if is not None:
                g.add_node(f"{stage.name}__guard", _subworkflow_guard_node)
                g.add_conditional_edges(
                    f"{stage.name}__guard",
                    _subworkflow_skip_router(stage, body_target, nxt),
                    {body_target: body_target, nxt: nxt},
                )
            g.add_conditional_edges(
                stage.name,
                _subworkflow_router,
                {"next": nxt, "rejected": reject_entry},
            )
        else:  # Step
            g.add_node(stage.name, _step_node(stage))
            g.add_edge(stage.name, nxt)

    g.add_edge(START, entries[0] if entries else "complete")
    g.add_edge("complete", END)
    g.add_edge("rejected", END)
    return g


def _gate_router(gate_name: str, next_node: str):
    def router(state: WorkflowState) -> str:
        return "next" if state.get("gates", {}).get(gate_name) else "rejected"

    return router


def _subworkflow_router(state: WorkflowState) -> str:
    """Route to 'rejected' when the nested workflow rejected, else continue."""
    return "rejected" if state.get("rejected") else "next"
