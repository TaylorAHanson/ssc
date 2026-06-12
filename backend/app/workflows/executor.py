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

from app.core.exceptions import PermanentError
from app.workflows.checkpointer import build_checkpointer
from app.workflows.graphs import build_graph_for, has_graph

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
        except KeyError as e:
            # No published workflow and no code spec for this type. Retrying can
            # never resolve this (the workflow was deleted/unpublished, or the
            # type was never registered), so surface it as permanent instead of
            # letting the poller burn retries on identical tracebacks.
            req_type = getattr(request.type, "value", request.type)
            raise PermanentError(
                f"No workflow graph registered for request type '{req_type}'. "
                "Its workflow may have been deleted or unpublished."
            ) from e
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
            elif self._is_stalled(snapshot):
                # State exists with pending next-nodes but the graph is NOT paused
                # on a HITL interrupt — it stalled mid-run (a node raised, or the
                # process crashed during a superstep). Resume from the checkpoint so
                # the pending node re-runs. Mutating tools are idempotency-keyed, so
                # re-running never double-applies a side effect.
                logger.info("[%s] resuming stalled graph at %s",
                            request.id, tuple(snapshot.next or ()))
                await compiled.ainvoke(None, config)
            else:
                logger.debug("[%s] advance() with no resume; awaiting interrupt", request.id)

            return await self._interpret(compiled, config)

    @staticmethod
    def _is_stalled(snapshot) -> bool:
        """True if the graph has pending work but isn't waiting on an interrupt.

        Distinguishes a mid-run stall (node error / crash, which we should resume)
        from a HITL gate pause (which must wait for an approval decision).
        """
        pending = bool(snapshot.next)
        waiting_on_interrupt = any(
            getattr(task, "interrupts", ()) for task in (snapshot.tasks or ())
        )
        return pending and not waiting_on_interrupt

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
    """Map a graph status to the existing RequestStatus enum for UI parity.

    Graph statuses mirror the enum *values* (``provisioning``,
    ``manager_approval``, ``data_owner_approval``, ``training_pending``,
    ``completed``/``rejected``/``failed``, ``pending``), so we resolve by value.
    The previous hand-maintained name-based dict mapped ``provisioning`` (and the
    unlisted ``manager_approval`` / ``training_pending``) to a non-existent
    ``IN_PROGRESS`` member and silently fell back to ``PENDING``. That broke the
    poller's ``is_long_running = status == "provisioning"`` check, so genuinely
    long provisioning runs never got the long lock timeout + heartbeat and their
    lock could expire mid-flight (allowing a second worker to claim them).

    An unrecognized, non-empty status means the graph is still doing work, so we
    map it to PROVISIONING (active) rather than the old silent PENDING.
    """
    from app.models.request import RequestStatus

    if not status_str:
        return RequestStatus.PENDING
    try:
        return RequestStatus(status_str)
    except ValueError:
        return RequestStatus.PROVISIONING


# Module-level singleton (mirrors app.tools.tool_executor.executor).
executor = DurableWorkflowExecutor()
