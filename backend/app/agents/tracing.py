"""
MLflow tracing wiring for the agent (Databricks best practice).

Provides a thin, dependency-tolerant layer so the agent loop can emit MLflow
traces (one per turn, with child spans for each LLM call and tool execution)
without hard-coupling the request path to MLflow. When tracing is disabled (or
mlflow isn't importable) every helper degrades to a no-op and the agent runs
unchanged.

Traces land in the configured MLflow experiment; with Databricks tracking they
back the inference-table + LLM-as-judge observability story. The per-turn
``trace_id`` is surfaced to the UI so feedback can be attached to the exact run.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_initialized = False
_active = False


def init_tracing() -> None:
    """Initialize MLflow tracking + autolog once at startup. Safe to call always."""
    global _initialized, _active
    if _initialized:
        return
    _initialized = True
    if not settings.MLFLOW_TRACING_ENABLED:
        logger.info("MLflow tracing disabled (MLFLOW_TRACING_ENABLED=false).")
        return
    try:
        import mlflow

        if settings.MLFLOW_TRACKING_URI:
            mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
        if settings.MLFLOW_EXPERIMENT:
            mlflow.set_experiment(settings.MLFLOW_EXPERIMENT)
        _active = True
        logger.info(
            "MLflow tracing enabled (uri=%s experiment=%s).",
            settings.MLFLOW_TRACKING_URI or "<default>",
            settings.MLFLOW_EXPERIMENT or "<default>",
        )
    except Exception as e:  # noqa: BLE001 - tracing must never break the agent
        logger.warning("MLflow tracing init failed; continuing without it: %s", e)
        _active = False


def tracing_active() -> bool:
    return _active


@contextmanager
def span(name: str, span_type: str = "UNKNOWN",
         inputs: Optional[Dict[str, Any]] = None,
         attributes: Optional[Dict[str, Any]] = None) -> Iterator[Any]:
    """Open an MLflow span if tracing is active; otherwise a no-op context.

    Yields the span object (or ``None``) so callers can set outputs.
    """
    if not _active:
        yield None
        return
    try:
        import mlflow
        from mlflow.entities import SpanType

        stype = getattr(SpanType, span_type, SpanType.UNKNOWN)
        with mlflow.start_span(name=name, span_type=stype) as s:
            try:
                if inputs is not None:
                    s.set_inputs(inputs)
                if attributes:
                    for k, v in attributes.items():
                        s.set_attribute(k, v)
            except Exception:  # noqa: BLE001
                pass
            yield s
    except Exception as e:  # noqa: BLE001
        logger.debug("span(%s) failed; running without span: %s", name, e)
        yield None


def set_span_outputs(s: Any, outputs: Any) -> None:
    if s is None:
        return
    try:
        s.set_outputs(outputs)
    except Exception:  # noqa: BLE001
        pass


def start_root_span(name: str, inputs: Optional[Dict[str, Any]] = None) -> Optional[tuple]:
    """Open a long-lived root span that stays open across generator yields.

    Returns an opaque handle (or ``None``) to pass to :func:`end_root_span`.
    All child spans opened via :func:`span` while this is open share its trace,
    so a whole agent turn becomes one trace. Read ``handle[1].trace_id`` for the
    id to surface to the UI.
    """
    if not _active:
        return None
    try:
        import mlflow
        from mlflow.entities import SpanType

        cm = mlflow.start_span(name=name, span_type=SpanType.AGENT)
        s = cm.__enter__()
        if inputs is not None:
            try:
                s.set_inputs(inputs)
            except Exception:  # noqa: BLE001
                pass
        return (cm, s)
    except Exception as e:  # noqa: BLE001
        logger.debug("start_root_span failed: %s", e)
        return None


def root_trace_id(handle: Optional[tuple]) -> Optional[str]:
    if not handle:
        return None
    try:
        return str(handle[1].trace_id)
    except Exception:  # noqa: BLE001
        return None


def end_root_span(handle: Optional[tuple], outputs: Any = None) -> None:
    if not handle:
        return
    cm, s = handle
    try:
        if outputs is not None:
            s.set_outputs(outputs)
    except Exception:  # noqa: BLE001
        pass
    try:
        cm.__exit__(None, None, None)
    except Exception:  # noqa: BLE001
        pass


def current_trace_id() -> Optional[str]:
    """Return the id of the most recently completed/active trace, if any."""
    if not _active:
        return None
    try:
        import mlflow

        tid = mlflow.get_last_active_trace_id()
        return str(tid) if tid else None
    except Exception:  # noqa: BLE001
        return None


def log_feedback(trace_id: str, value: Any, comment: Optional[str] = None,
                 user: Optional[str] = None) -> bool:
    """Attach user feedback to a trace (thumbs up/down, rating, comment).

    Returns True if the feedback was recorded to MLflow. No-op (False) when
    tracing is inactive. Feedback keyed by ``trace_id`` is what the scheduled
    LLM-as-judge job and quality dashboards correlate against.
    """
    if not _active or not trace_id:
        return False
    try:
        import mlflow

        mlflow.log_feedback(
            trace_id=trace_id,
            name="user_feedback",
            value=value,
            rationale=comment,
            source=mlflow.entities.AssessmentSource(
                source_type="HUMAN", source_id=user or "user"
            ),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("log_feedback failed for trace %s: %s", trace_id, e)
        return False


def update_trace_metadata(tags: Dict[str, Any]) -> None:
    """Attach tags/metadata (user, request_id, etc.) to the current trace."""
    if not _active:
        return
    try:
        import mlflow

        mlflow.update_current_trace(tags=tags)
    except Exception:  # noqa: BLE001
        pass
