"""
V2 UI-state renderer.

Replaces the legacy ``BaseRequestStateMachine.to_state_machine_state()``. Builds
the same ``StateMachineState`` the frontend expects, derived from the request's
declarative stage list + the immutable fact log + ``request.status`` (kept
current by the V2 poller). Synchronous so the existing API call sites need no
async refactor.

Progress model: pending -> [gates/steps...] -> completed. A gate is "passed"
when its approval/event fact is present; provision steps run instantly after
their gate, so the first stage *not* satisfied is the active one (where the
user is waiting). Terminal statuses (completed/rejected/failed) short-circuit.
"""
import logging
from typing import Any, Dict, List

from app.models.request import StateInfo, StateMachineState
from app.workflows.graphs import stage_specs
from app.workflows.spec_loader import stage_specs_from_dict

logger = logging.getLogger(__name__)

_TERMINAL = {"completed", "rejected", "failed"}


def _humanize(node_id: str) -> str:
    return node_id.replace("_", " ").title()


def _gate_satisfied(gtype: str, have: set, facts: list) -> bool:
    """Whether a gate's approval/event fact is present (shared by views)."""
    if gtype in ("manager", "platform_admin", "data_owner"):
        return any(
            f.event_type == "approval_received"
            and (f.event_data or {}).get("approval_type") == gtype
            for f in facts
        )
    if gtype == "training":
        return "training_completed" in have
    if gtype == "pr_merge":
        return "pr_merged" in have
    if gtype == "children":
        return "all_children_completed" in have
    return False


def render_state(request, db, facts=None, spec_dict=None) -> StateMachineState:
    """Build the StateMachineState view for a request from stages + facts.

    ``facts`` and ``spec_dict`` may be passed in by list endpoints that have
    already batch-loaded them (one query for the whole page, plus one spec
    lookup per distinct request type) to avoid an N+1 of per-request fact and
    published-graph-spec queries. When omitted they're resolved here for the
    single-request call sites.
    """
    from app.state_machines.facts import get_facts

    status = request.status
    # Resolve stages the same DB-first way as the live graph view (published
    # DB graph_spec -> code catalog -> synthesized) so a dynamically-authored
    # workflow that lives only in the DB shows all its stages here too. Going
    # catalog-only (``stage_specs``) collapsed such requests to just
    # Created -> Completed because the type isn't in the bundled catalog.
    if spec_dict is None:
        spec_dict = _resolve_spec_dict(request, db)
    specs = stage_specs_from_dict(spec_dict)
    if facts is None:
        facts = get_facts(db, request.id)
    have = {f.event_type for f in facts}

    def gate_satisfied(gtype: str) -> bool:
        return _gate_satisfied(gtype, have, facts)

    # Determine the active node.
    if status in _TERMINAL:
        current = status
    else:
        current = "completed"
        for s in specs:
            if s["kind"] == "gate":
                satisfied = gate_satisfied(s["gate_type"])
            elif s["success_fact"]:
                satisfied = s["success_fact"] in have
            else:
                satisfied = True  # transient provision step; runs instantly
            if not satisfied:
                current = s["name"]
                break

    # Ordered UI ids.
    ids: List[str] = ["pending"] + [s["name"] for s in specs] + ["completed"]
    if current in ("rejected", "failed") and current not in ids:
        ids.append(current)

    cur_idx = ids.index(current) if current in ids else len(ids) - 1
    states_view: List[StateInfo] = []
    for idx, node_id in enumerate(ids):
        states_view.append(StateInfo(
            id=node_id,
            name="Created" if node_id == "pending" else _humanize(node_id),
            isActive=(node_id == current),
            isCompleted=(idx < cur_idx) or status == "completed",
            isInitial=(idx == 0),
            isFinal=node_id in _TERMINAL,
            completedAt=None,
            startedAt=request.created_at if idx == 0 else None,
            facts=None,
        ))

    return StateMachineState(currentState=current, states=states_view, currentProgress=None)


# --------------------------------------------------------------------------
# Live graph view (for the request-detail visual workflow runner)
# --------------------------------------------------------------------------
def _resolve_spec_dict(request, db) -> Dict[str, Any]:
    """The serializable graph_spec actually governing this request.

    Prefers a published workflow's authored ``graph_spec`` (the no-code override),
    then the code catalog, then a synthesized shape for dedicated graphs
    (e.g. data_access) so the UI always has something to draw.
    """
    from app.workflows.graphs import published_graph_spec
    from app.workflows.graphs.specs import SPECS

    spec = published_graph_spec(db, request.type)
    if spec:
        return spec
    key = getattr(request.type, "value", request.type)
    if key in SPECS:
        return SPECS[key]
    stages = []
    for s in stage_specs(request.type):
        if s["kind"] == "gate":
            stages.append({"kind": "gate", "name": s["name"], "type": s.get("gate_type") or "manager"})
        else:
            stages.append({"kind": "step", "name": s["name"], "tool": "(provision)",
                           "success_fact": s.get("success_fact")})
    return {"name": str(key), "stages": stages}


def live_graph(request, db) -> Dict[str, Any]:
    """Return the request's authored graph plus per-node live status.

    ``node_states`` keys match the graph node ids the frontend renders
    (``pending`` start, each stage name, ``complete``, ``rejected``); values are
    ``done`` | ``current`` | ``pending`` | ``rejected``. Derived from the same
    fact log + ``request.status`` the timeline uses, so the picture stays honest.
    """
    from app.state_machines.facts import get_facts

    spec_dict = _resolve_spec_dict(request, db)
    stages = spec_dict.get("stages", []) or []
    facts = get_facts(db, request.id)
    have = {f.event_type for f in facts}
    status = request.status
    ctx = getattr(request, "state_context", None) or {}

    def is_skipped(stage: Dict[str, Any]) -> bool:
        """A conditional step whose run_if is false for this request."""
        if stage.get("kind") != "step" or stage.get("run_if") is None:
            return False
        try:
            from app.workflows import expr
            return not bool(expr.evaluate(stage["run_if"], {"ctx": ctx, "item": None}))
        except Exception:  # noqa: BLE001 - never let a bad expr break the view
            return False

    def satisfied(stage: Dict[str, Any]) -> bool:
        if stage.get("kind") == "gate":
            return _gate_satisfied(stage.get("type"), have, facts)
        if is_skipped(stage):
            return True  # skipped steps don't block progression
        sf = stage.get("success_fact")
        return (sf in have) if sf else True

    node_states: Dict[str, str] = {"pending": "done"}
    current = status

    if status == "completed":
        for s in stages:
            node_states[s["name"]] = "skipped" if is_skipped(s) else "done"
        node_states["complete"] = "done"
        current = "complete"
    elif status in _TERMINAL:  # rejected / failed
        stopped = False
        for s in stages:
            if stopped:
                node_states[s["name"]] = "pending"
            elif is_skipped(s):
                node_states[s["name"]] = "skipped"
            elif satisfied(s):
                node_states[s["name"]] = "done"
            else:
                node_states[s["name"]] = "rejected"
                current = s["name"]
                stopped = True
        node_states["complete"] = "pending"
        node_states["rejected"] = "rejected"
    else:
        found = False
        for s in stages:
            if found:
                node_states[s["name"]] = "pending"
            elif is_skipped(s):
                node_states[s["name"]] = "skipped"
            elif satisfied(s):
                node_states[s["name"]] = "done"
            else:
                node_states[s["name"]] = "current"
                current = s["name"]
                found = True
        if not found:
            node_states["complete"] = "current"
            current = "complete"
        else:
            node_states["complete"] = "pending"

    return {
        "request_id": request.id,
        "request_type": getattr(request.type, "value", request.type),
        "status": status,
        "current": current,
        "graph_spec": spec_dict,
        "node_states": node_states,
    }
