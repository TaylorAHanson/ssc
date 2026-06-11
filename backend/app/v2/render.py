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
from typing import List

from app.models.request import StateInfo, StateMachineState
from app.v2.graphs import stage_specs

logger = logging.getLogger(__name__)

_TERMINAL = {"completed", "rejected", "failed"}


def _humanize(node_id: str) -> str:
    return node_id.replace("_", " ").title()


def render_state(request, db) -> StateMachineState:
    """Build the StateMachineState view for a request from stages + facts."""
    from app.state_machines.facts import get_facts

    status = request.status
    specs = stage_specs(request.type)
    facts = get_facts(db, request.id)
    have = {f.event_type for f in facts}

    def approval_present(gtype: str) -> bool:
        return any(
            f.event_type == "approval_received"
            and (f.event_data or {}).get("approval_type") == gtype
            for f in facts
        )

    def gate_satisfied(gtype: str) -> bool:
        if gtype in ("manager", "platform_admin", "data_owner"):
            return approval_present(gtype)
        if gtype == "training":
            return "training_completed" in have
        if gtype == "pr_merge":
            return "pr_merged" in have
        if gtype == "children":
            return "all_children_completed" in have
        return False

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
