"""
Data Access workflow as a V2 LangGraph graph.

V1 equivalent: ``app.state_machines.data_access.DataAccessStateMachine``
(pending -> data_owner_approval -> provisioning -> completed).

V2 shape:
    resolve_owners -> [interrupt: data-owner approval] -> provision -> complete
                                                       (or) -> rejected

Durability is the checkpointer (state survives crashes); the approval gate is a
native LangGraph ``interrupt()`` instead of the V1 ``wait_for_event`` + poller
fact polling. Each grant runs through the shared ``ToolExecutor`` (mutating
``data_grant`` tool) so it is OPA-gated, idempotent, and audited.
"""
import logging
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

logger = logging.getLogger(__name__)


class DataAccessState(TypedDict, total=False):
    request_id: str
    context: Dict[str, Any]          # request.state_context (assets, access_level, ...)
    data_owners: List[str]
    approved: bool
    rejection_reason: Optional[str]
    grant_results: List[Dict[str, Any]]
    status: str                      # mirrors RequestStatus for UI parity


def _assets(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    assets = ctx.get("assets", [])
    if not assets and ctx.get("asset_name"):
        assets = [{"asset_name": ctx.get("asset_name"), "asset_type": ctx.get("asset_type")}]
    return assets


async def _resolve_owners(state: DataAccessState) -> DataAccessState:
    """Resolve data owners from UC tags (approver_group), falling back to owner.

    Mirrors V1 ``on_enter_data_owner_approval_async`` owner resolution. Best
    effort: if the provider is unavailable we proceed with whatever the context
    already carries so the approval gate still renders.
    """
    ctx = state["context"]
    owners = list(ctx.get("data_owners") or [])
    if not owners:
        try:
            from app.v2.tools import _get_databricks_provider
            provider = _get_databricks_provider()
            found = set()
            for asset in _assets(ctx):
                name, atype = asset.get("asset_name"), asset.get("asset_type")
                if not (name and atype):
                    continue
                from app.core.config import settings
                tag_key = settings.APPROVER_GROUP_TAG_KEY
                tags = await provider.get_asset_tags(atype, name, [tag_key])
                grp = tags.get(tag_key)
                if grp:
                    found.add(grp)
                else:
                    owner = await provider.get_asset_owner(atype, name)
                    if owner:
                        found.add(owner)
            owners = sorted(found)
        except Exception as e:
            logger.warning("[%s] owner resolution degraded: %s", state["request_id"], e)
    return {"data_owners": owners, "status": "data_owner_approval"}


async def _await_approval(state: DataAccessState) -> DataAccessState:
    """Pause for the data owner(s). Resumes with ``{"approved": bool, ...}``.

    The durable interrupt persists in the checkpointer; the executor resumes the
    thread once the approval API records a decision.
    """
    decision = interrupt(
        {
            "type": "data_owner_approval",
            "request_id": state["request_id"],
            "data_owners": state.get("data_owners", []),
        }
    )
    if isinstance(decision, dict):
        return {
            "approved": bool(decision.get("approved")),
            "rejection_reason": decision.get("reason"),
        }
    return {"approved": bool(decision)}


def _route_after_approval(state: DataAccessState) -> str:
    return "provision" if state.get("approved") else "rejected"


async def _provision(state: DataAccessState) -> DataAccessState:
    """Grant access via the data_grant tool, once per asset, through the ToolExecutor."""
    from app.db.session import get_db
    from app.state_machines.facts import add_fact
    from app.tools.tool_executor import ToolContext, executor
    from app.v2.tools import grant_uc_access

    ctx = state["context"]
    principal = ctx.get("requested_by_email")
    access_level = ctx.get("access_level")
    request_id = state["request_id"]
    results: List[Dict[str, Any]] = []

    db = next(get_db())
    try:
        for idx, asset in enumerate(_assets(ctx)):
            # Per-asset idempotency key so a crash mid-provision replays safely.
            tool_ctx = ToolContext(
                tool_call_id=f"grant:{idx}:{asset.get('asset_name')}",
                user_identity={"email": principal},
                db=db,
                scope_id=request_id,
                approvals=["data_owner"],  # gate already cleared by _await_approval
            )
            res = await executor.run(
                grant_uc_access,
                tool_ctx,
                asset_type=asset.get("asset_type"),
                asset_name=asset.get("asset_name"),
                principal=principal,
                access_level=access_level,
            )
            results.append(res)
        add_fact(
            db, request_id, "access_granted",
            {"access_level": access_level, "principal": principal, "results": results},
            actor="system",
        )
    finally:
        db.close()
    return {"grant_results": results, "status": "completed"}


async def _complete(state: DataAccessState) -> DataAccessState:
    logger.info("[%s] data access completed", state["request_id"])
    return {"status": "completed"}


async def _rejected(state: DataAccessState) -> DataAccessState:
    from app.db.session import get_db
    from app.state_machines.facts import add_fact

    db = next(get_db())
    try:
        add_fact(
            db, state["request_id"], "request_rejected",
            {"reason": state.get("rejection_reason")}, actor="data_owner",
        )
    finally:
        db.close()
    return {"status": "rejected"}


def build_graph() -> StateGraph:
    """Build (uncompiled) the data-access StateGraph. The executor compiles it
    with the checkpointer."""
    g = StateGraph(DataAccessState)
    g.add_node("resolve_owners", _resolve_owners)
    g.add_node("await_approval", _await_approval)
    g.add_node("provision", _provision)
    g.add_node("complete", _complete)
    g.add_node("rejected", _rejected)

    g.add_edge(START, "resolve_owners")
    g.add_edge("resolve_owners", "await_approval")
    g.add_conditional_edges(
        "await_approval", _route_after_approval,
        {"provision": "provision", "rejected": "rejected"},
    )
    g.add_edge("provision", "complete")
    g.add_edge("complete", END)
    g.add_edge("rejected", END)
    return g
