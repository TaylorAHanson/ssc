"""
V2 durable workflow executor.

The single seam the poller will call at the M5 cutover (replacing
``load_state_machine -> tick() -> save -> execute_tasks()``). It compiles the
request's graph against the durable checkpointer, advances it, and reports a
status the existing UI can render.

Lifecycle per request (keyed on ``request.id`` as the LangGraph thread):
  * first advance  -> run from START until it interrupts (HITL) or finishes
  * resume(value)  -> inject an approval decision and continue
  * advance()      -> no-op while waiting on an unmet interrupt

A crash between nodes resumes from the last checkpoint; mutating nodes are
idempotent (ToolExecutor idempotency keys), so re-running an interrupted graph
never double-applies a side effect.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from langgraph.types import Command

from app.v2.checkpointer import build_checkpointer
from app.v2.graphs import build_graph_for, has_graph

logger = logging.getLogger(__name__)


@dataclass
class AdvanceResult:
    status: str                       # graph's status field (UI-mapped below)
    interrupted: bool                 # paused on a HITL gate
    interrupt_payload: Optional[Any]  # what the gate is asking for
    done: bool                        # terminal (no next nodes)
    values: Dict[str, Any]            # current graph state
    current_node: Optional[str] = None  # node paused/next to run (None = not started)
    next_nodes: tuple = ()


class DurableWorkflowExecutor:
    """Runs/resumes a request's V2 graph on the durable checkpointer."""

    def can_handle(self, request) -> bool:
        return has_graph(request.type)

    def _initial_state(self, request) -> Dict[str, Any]:
        return {
            "request_id": request.id,
            "context": request.state_context or {},
            "status": "pending",
            "grant_results": [],
        }

    def _build_graph(self, request):
        """Resolve the request's graph, preferring a published DB graph_spec."""
        from app.db.session import get_db

        db = None
        try:
            db = next(get_db())
        except Exception:  # noqa: BLE001 - no DB (e.g. harness) -> code catalog
            db = None
        try:
            return build_graph_for(request.type, db)
        finally:
            if db is not None:
                db.close()

    async def advance(self, request, resume_value: Optional[Any] = None) -> AdvanceResult:
        graph = self._build_graph(request)
        async with build_checkpointer() as cp:
            compiled = graph.compile(checkpointer=cp)
            config = {"configurable": {"thread_id": request.id}}

            snapshot = await compiled.aget_state(config)
            has_state = bool(snapshot.values)

            if not has_state:
                await compiled.ainvoke(self._initial_state(request), config)
            elif resume_value is not None:
                await compiled.ainvoke(Command(resume=resume_value), config)
            else:
                logger.debug("[%s] advance() with no resume; awaiting input", request.id)

            return await self._interpret(compiled, config)

    async def resume(self, request, resume_value: Any) -> AdvanceResult:
        return await self.advance(request, resume_value=resume_value)

    async def peek(self, request) -> AdvanceResult:
        """Read current graph state WITHOUT advancing (for UI rendering)."""
        graph = self._build_graph(request)
        async with build_checkpointer() as cp:
            compiled = graph.compile(checkpointer=cp)
            config = {"configurable": {"thread_id": request.id}}
            return await self._interpret(compiled, config)

    async def _interpret(self, compiled, config) -> AdvanceResult:
        state = await compiled.aget_state(config)
        values = dict(state.values or {})
        interrupts = []
        for task in (state.tasks or ()):
            interrupts.extend(getattr(task, "interrupts", ()) or ())
        interrupted = bool(interrupts)
        next_nodes = tuple(state.next or ())
        done = not next_nodes and not interrupted and bool(values)
        payload = interrupts[0].value if interrupts else None
        return AdvanceResult(
            status=values.get("status", "pending"),
            interrupted=interrupted,
            interrupt_payload=payload,
            done=done,
            values=values,
            current_node=next_nodes[0] if next_nodes else None,
            next_nodes=next_nodes,
        )


def to_request_status(status_str: str):
    """Best-effort map a graph status to the existing RequestStatus enum for UI parity."""
    from app.models.request import RequestStatus

    mapping = {
        "pending": "PENDING",
        "data_owner_approval": "DATA_OWNER_APPROVAL",
        "provisioning": "IN_PROGRESS",
        "completed": "COMPLETED",
        "rejected": "REJECTED",
        "failed": "FAILED",
    }
    name = mapping.get(status_str, "IN_PROGRESS")
    return getattr(RequestStatus, name, getattr(RequestStatus, "PENDING", None))


# Module-level singleton (mirrors app.tools.tool_executor.executor).
executor = DurableWorkflowExecutor()
