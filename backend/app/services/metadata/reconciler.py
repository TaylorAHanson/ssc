"""The reconciler: queue, runs, modes and circuit breaker (ADR-0006 D3). Workstream C.

``run`` reconciles a scope: named objects (a queue batch) or whole catalogs (a sweep).
It refreshes the snapshot for that scope, diffs every managed row against UC under
full enforcement, then records the plan (``plan``) or applies it and records the
outcome (``enforce``). Catalogs outside
METADATA_RECONCILE_CATALOGS (``scope``) are never touched, and enforce needs that list
to be set. A run that would remove more tags or descriptions than
METADATA_MAX_REMOVALS_PER_RUN trips the circuit breaker and applies nothing.

``drain_queue`` runs the oldest due queue rows as one run, deletes the rows that
converged and backs off the rest. ``reconcile_task`` is the poller's entry point
(leader only): the new-object poll on its interval, the queue drained in batches
within a time budget, and the cron sweep (``METADATA_RECONCILE_CRON``) when due.

One run at a time, under a Postgres advisory lock. Settings are read at call time,
so Admin -> Settings edits apply on the next cycle.
"""
from __future__ import annotations

import asyncio
import contextvars
import html
import logging
import os
import socket
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from croniter import CroniterBadCronError, croniter
from sqlalchemy import and_, bindparam, delete, func, insert, select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_formatter import job_log_context
from app.db.metadata import (
    OBJECT_ACTIVE,
    MetadataObjectModel,
    MetadataObservedModel,
    MetadataReconcileItemModel,
    MetadataReconcileQueueModel,
    MetadataReconcileRunModel,
    MetadataSyncStateModel,
)
from app.services.metadata import apply as apply_
from app.services.metadata import diff as diff_
from app.services.metadata import errors, policy, scope
from app.services.metadata import observed as snapshot
from app.services.metadata.types import (
    COLUMN_SEPARATOR,
    RELATION_TYPES,
    DesiredState,
    ObjectKey,
    ObservedState,
    PlanItem,
    PlanOutcome,
    ReconcileMode,
    ReconcileResult,
    ReconcileRunPage,
    RunStatus,
    RunTrigger,
    normalize_value,
)

logger = logging.getLogger(__name__)

# Cached next sweep; settings_store resets it when METADATA_RECONCILE_CRON changes.
_next_sweep_time: Optional[datetime] = None
# The invalid cron or mode already logged, so a bad value is reported once.
_bad_cron: Optional[str] = None
_bad_mode: Optional[str] = None
# Monotonic time of the last new-object poll.
_last_poll_at: Optional[float] = None
# The breaker trip and the pass error already logged, so the poller doesn't repeat them every cycle.
_breaker_noted: Optional[str] = None
_pass_error_noted: Optional[str] = None
# Manual runs the API detaches so the HTTP request can return while the sweep continues.
_live_runs: Set[threading.Thread] = set()
# How long POST /runs waits for a sweep to finish before returning the still-running row.
_ACCEPT_SECONDS = 2.0

MODES = ("off", "plan", "enforce")
CIRCUIT_BREAKER_KEY = "reconciler.circuit_breaker"
# Session-level Postgres advisory lock key that keeps reconcile runs single flight across replicas.
_ADVISORY_LOCK_KEY = 5_006_202_610_200_001
_MAX_PAGE_SIZE = 500
_IN_CHUNK = 500
_WRITE_CHUNK = 1000

# Queue rows per drain, and the time one poller pass may spend draining before it yields.
_BATCH_SIZE = 200
_PASS_BUDGET_SECONDS = 50.0
# A failing queue row is retried after 30 s, doubling up to an hour; governance hears about it at 5 attempts.
_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600
_DEFAULT_APPLY_WORKERS = 8
# Desired rows planned per step of a sweep (whole relations, so a relation's columns stay together).
_SWEEP_CHUNK = 2000
_MAX_ERROR = 2000
# Queue reasons whose unmanaged objects the new-object policy adopts. Other reasons
# (a change, a manual run) name objects that should already have desired state.
_ADOPT_REASONS = ("new_object", "notify")
_ADOPT_REASON = "New in Unity Catalog with no desired state; adopted as observed (METADATA_NEW_OBJECT_POLICY=adopt)."

_QUEUE = MetadataReconcileQueueModel.__table__
_OBJECTS = MetadataObjectModel.__table__


def queue_key(fqn_key: str) -> str:
    """Columns are reconciled with their relation, so the queue holds relation-level keys."""
    return ObjectKey.from_fqn_key(fqn_key).relation_key().fqn_key


def enqueue(
    db: Session,
    fqn_keys: Iterable[str],
    *,
    reason: str = "change",
    requested_by: Optional[str] = None,
    change_set_id: Optional[str] = None,
) -> List[str]:
    """Add objects to the queue (deduplicated per relation), due now. Doesn't commit: callers enqueue inside their transaction.

    Re-enqueueing a row that was backing off makes it due now with a fresh attempt count.
    """
    keys = sorted({queue_key(k) for k in fqn_keys if k})
    if not keys:
        return []
    existing: Dict[str, MetadataReconcileQueueModel] = {}
    for start in range(0, len(keys), _IN_CHUNK):
        chunk = keys[start:start + _IN_CHUNK]
        for row in db.query(MetadataReconcileQueueModel).filter(MetadataReconcileQueueModel.fqn_key.in_(chunk)):
            existing[row.fqn_key] = row
    now = _utcnow()
    for key in keys:
        row = existing.get(key)
        if row is None:
            db.add(MetadataReconcileQueueModel(
                fqn_key=key, reason=reason, requested_by=requested_by, change_set_id=change_set_id,
                enqueued_at=now, not_before=now,
            ))
        else:
            row.reason = reason
            row.requested_by = requested_by or row.requested_by
            row.change_set_id = change_set_id or row.change_set_id
            row.not_before = now
            row.attempts = 0
            row.last_error = None
    return keys


def current_mode() -> ReconcileMode:
    """METADATA_RECONCILE_MODE; blank or unknown is ``plan`` (writes nothing), and so is enforce with no catalog list."""
    global _bad_mode
    raw = str(getattr(settings, "METADATA_RECONCILE_MODE", "") or "").strip().lower()
    if raw == "enforce" and not scope.is_explicit():
        if _bad_mode != "enforce":
            logger.warning("METADATA_RECONCILE_MODE is enforce but METADATA_RECONCILE_CATALOGS is blank; "
                           "reconciling in plan mode (no writes).")
            _bad_mode = "enforce"
        return "plan"
    if raw in MODES:
        return raw  # type: ignore[return-value]
    if raw and raw != _bad_mode:
        logger.warning("Unknown METADATA_RECONCILE_MODE '%s'; reconciling in plan mode (no writes).", raw)
        _bad_mode = raw
    return "plan"


def _int_setting(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _adopt_new_objects() -> bool:
    """METADATA_NEW_OBJECT_POLICY: ``adopt`` (the default) or ``ignore``."""
    return str(getattr(settings, "METADATA_NEW_OBJECT_POLICY", "") or "adopt").strip().lower() != "ignore"


_OUTSIDE_SCOPE = "Not one of this deployment's catalogs (Catalogs to reconcile), so it's left alone."
_REPORTED_DETAIL = "Outside the schemas this deployment enforces (Enforce only in these schemas), so it's only reported."


def _removal_limit() -> int:
    return max(0, _int_setting("METADATA_MAX_REMOVALS_PER_RUN", 200))


def _apply_workers() -> int:
    """Statements run in parallel per run (relations stay sequential); METADATA_APPLY_CONCURRENCY if set."""
    return max(1, _int_setting("METADATA_APPLY_CONCURRENCY", _DEFAULT_APPLY_WORKERS))


# --- Sync state and the circuit breaker -----------------------------------------------


def _utcnow() -> datetime:
    """Naive UTC, like every DateTime column in the metadata tables."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _monotonic() -> float:
    return time.monotonic()


def _ms(since: float) -> int:
    return int((_monotonic() - since) * 1000)


def _clip(message: Optional[str], limit: int = _MAX_ERROR) -> Optional[str]:
    if message is None:
        return None
    message = str(message)
    return message if len(message) <= limit else message[:limit] + "..."


def _get_state(db: Session, key: str) -> Optional[dict]:
    row = db.get(MetadataSyncStateModel, key)
    return row.value if row is not None else None


def _set_state(db: Session, key: str, value: Optional[dict]) -> None:
    row = db.get(MetadataSyncStateModel, key)
    if row is None:
        db.add(MetadataSyncStateModel(key=key, value=value, updated_at=_utcnow()))
    else:
        row.value = value
        row.updated_at = _utcnow()
    db.flush()


def circuit_breaker_state(db: Session) -> Optional[dict]:
    """The trip record (run id, removals, limit, reason, tripped_at) while tripped, else None."""
    value = _get_state(db, CIRCUIT_BREAKER_KEY)
    return value if isinstance(value, dict) and value.get("tripped") else None


def circuit_breaker_tripped(db: Session) -> bool:
    return circuit_breaker_state(db) is not None


def trip_circuit_breaker(
    db: Session, *, run_id: str, removals: int, limit: int, reason: Optional[str] = None,
) -> dict:
    """Record that a run was paused for too many removals. Doesn't commit."""
    value = {
        "tripped": True,
        "run_id": run_id,
        "removals": int(removals),
        "limit": int(limit),
        "reason": reason or f"The run would remove {removals} tags or descriptions; the limit is {limit}.",
        "tripped_at": _utcnow().isoformat(),
    }
    _set_state(db, CIRCUIT_BREAKER_KEY, value)
    return value


def clear_circuit_breaker(db: Session, *, cleared_by: Optional[str] = None) -> bool:
    """Clear a tripped breaker (keeping the last trip for reference); True if it was tripped. Doesn't commit."""
    previous = circuit_breaker_state(db)
    if previous is None:
        return False
    _set_state(db, CIRCUIT_BREAKER_KEY, {
        "tripped": False, "cleared_by": cleared_by, "cleared_at": _utcnow().isoformat(), "last_trip": previous,
    })
    return True


def _paused_reason(breaker: dict) -> str:
    return (
        f"The circuit breaker is tripped: {breaker.get('reason') or 'too many removals.'} "
        "Nothing is applied until an admin reviews the paused run and runs again with the override."
    )


# --- Runs ------------------------------------------------------------------------------


def _split_fqn_key(fqn_key: str):
    relation, _, column = fqn_key.partition(COLUMN_SEPARATOR)
    return relation, (column or None)


def _relation_of(fqn_key: str) -> str:
    return fqn_key.partition(COLUMN_SEPARATOR)[0]


def _catalog_of(fqn_key: str) -> str:
    return _relation_of(fqn_key).split(".", 1)[0]


def _level_type(fqn_key: str) -> str:
    relation, column = _split_fqn_key(fqn_key)
    if column:
        return "column"
    return {1: "catalog", 2: "schema"}.get(len(relation.split(".")), "table")


def _securable_types(db: Session, fqn_keys: Iterable[str]) -> Dict[str, str]:
    """Each relation's type for display: UC's (the snapshot) over the desired row's."""
    relations = sorted({k for k in fqn_keys if _level_type(k) == "table"})
    found: Dict[str, str] = {}
    for start in range(0, len(relations), _IN_CHUNK):
        chunk = relations[start:start + _IN_CHUNK]
        for model in (MetadataObjectModel, MetadataObservedModel):
            for fqn_key, stype in db.query(model.fqn_key, model.securable_type).filter(model.fqn_key.in_(chunk)):
                found[fqn_key] = stype
    return found


def _outcome_from_row(row: MetadataReconcileItemModel, types: Dict[str, str]) -> PlanOutcome:
    """A stored item as an outcome. Items written before the payload column carry only the statement.

    Older runs stored a catalog-claim statement (a SET TAGS) as operation ``claim``; it reads as ``set``.
    """
    relation, column = _split_fqn_key(row.fqn_key)
    level = _level_type(row.fqn_key)
    payload = row.payload if isinstance(row.payload, dict) else {}
    stype = row.securable_type or (types.get(row.fqn_key, level) if level == "table" else level)
    return PlanOutcome(
        fqn_key=row.fqn_key,
        object_id=row.object_id,
        name=relation,
        column=column,
        securable_type=stype,
        operation="set" if row.operation == "claim" else row.operation,
        sql=row.sql or "",
        tags=dict(payload.get("tags") or {}),
        keys=list(payload.get("keys") or []),
        comment=payload.get("comment"),
        removals=row.removals or 0,
        status=row.status,
        detail=row.detail,
    )


def _outcomes_from_rows(db: Session, rows: Sequence[MetadataReconcileItemModel]) -> List[PlanOutcome]:
    types = _securable_types(db, (r.fqn_key for r in rows if not r.securable_type))
    return [_outcome_from_row(r, types) for r in rows]


def _result(run: MetadataReconcileRunModel, items: Optional[List[PlanOutcome]] = None) -> ReconcileResult:
    skipped = (run.summary or {}).get("skipped")
    unread = (run.summary or {}).get("unread")
    scope_ = (run.summary or {}).get("scope")
    return ReconcileResult(
        run_id=run.id,
        trigger=run.trigger,
        mode=run.mode,
        status=run.status,
        actor=run.actor,
        objects_checked=run.objects_checked or 0,
        objects_drifted=run.objects_drifted or 0,
        statements_planned=run.statements_planned or 0,
        statements_applied=run.statements_applied or 0,
        statements_failed=run.statements_failed or 0,
        removals_planned=run.removals_planned or 0,
        statements_reported=int((run.summary or {}).get("reported") or 0),
        skipped_catalogs=list(run.skipped_catalogs or []),
        skipped_reasons=dict(skipped) if isinstance(skipped, dict) else {},
        unread_catalogs=[str(c) for c in unread] if isinstance(unread, list) else [],
        full_check=isinstance(scope_, dict) and "objects" not in scope_,
        paused_reason=run.paused_reason,
        error=run.error,
        items=items or [],
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def run_status(outcomes: Sequence[PlanOutcome], *, mode: ReconcileMode) -> RunStatus:
    """``completed`` unless enforcement failed: ``partial`` with some success, else ``failed``."""
    if mode != "enforce":
        return "completed"
    failed = sum(1 for o in outcomes if o.status == "failed")
    if not failed:
        return "completed"
    return "partial" if any(o.status in ("applied", "noop") for o in outcomes) else "failed"


# summary key naming the process running a run (host:pid), dropped when the run finishes.
_WORKER_KEY = "worker"
# A run still running this long after it started stopped without finishing, wherever it ran.
_ABANDONED_RUN_AGE = timedelta(hours=6)
_ABANDONED_RUN_ERROR = (
    "This run stopped before it finished, most likely because the app restarted while it was working. "
    "Later runs aren't affected."
)


def _worker() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _worker_gone(worker: object) -> bool:
    """True when ``worker`` (host:pid) is a process on this host that no longer exists."""
    host, _, pid = str(worker or "").rpartition(":")
    if host != socket.gethostname() or not pid.isdigit() or int(pid) == os.getpid():
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _close_abandoned_runs(db: Session) -> None:
    cutoff = _utcnow() - _ABANDONED_RUN_AGE
    running = db.query(MetadataReconcileRunModel).filter(MetadataReconcileRunModel.status == "running").all()
    for run in running:
        if run.started_at >= cutoff and not _worker_gone((run.summary or {}).get(_WORKER_KEY)):
            continue
        logger.warning("Metadata: reconcile run %s (started %s) stopped without finishing; closing it as failed.",
                       run.id, run.started_at)
        run.status = "failed"
        run.error = _ABANDONED_RUN_ERROR
        run.finished_at = _utcnow()
        run.summary = {k: v for k, v in (run.summary or {}).items() if k != _WORKER_KEY}


def start_run(
    db: Session, *, trigger: RunTrigger, mode: ReconcileMode, actor: Optional[str] = None,
    summary: Optional[dict] = None,
) -> str:
    """Insert a ``running`` run so the Runs tab shows it while it works. Commits; returns the run id.

    Runs left ``running`` by a process that stopped mid-run are closed as failed first.
    """
    _close_abandoned_runs(db)
    run = MetadataReconcileRunModel(
        id=str(uuid.uuid4()), trigger=trigger, mode=mode, status="running", actor=actor,
        skipped_catalogs=[], summary={**(summary or {}), _WORKER_KEY: _worker()}, started_at=_utcnow(),
    )
    db.add(run)
    db.commit()
    return run.id


def finish_run(
    db: Session,
    run_id: str,
    *,
    status: RunStatus,
    outcomes: Sequence[PlanOutcome] = (),
    objects_checked: int = 0,
    objects_drifted: int = 0,
    skipped_catalogs: Sequence[str] = (),
    paused_reason: Optional[str] = None,
    error: Optional[str] = None,
    summary: Optional[dict] = None,
) -> ReconcileResult:
    """Write the run's statements and counters, close it and commit. Raises ``errors.NotFound``."""
    run = db.get(MetadataReconcileRunModel, run_id)
    if run is None:
        raise errors.NotFound(f"Reconcile run '{run_id}' not found.")
    now = _utcnow()
    rows = [
        {
            "run_id": run_id,
            "object_id": o.object_id,
            "fqn_key": o.fqn_key,
            "securable_type": o.securable_type,
            "operation": o.operation,
            "sql": o.sql or "",
            "removals": int(o.removals or 0),
            "payload": {"tags": dict(o.tags), "keys": list(o.keys), "comment": o.comment},
            "status": o.status,
            "detail": o.detail,
            "created_at": now,
        }
        for o in outcomes
    ]
    for start in range(0, len(rows), _WRITE_CHUNK):
        db.execute(insert(MetadataReconcileItemModel), rows[start:start + _WRITE_CHUNK])
    run.status = status
    run.objects_checked = int(objects_checked)
    run.objects_drifted = int(objects_drifted)
    written = [o for o in outcomes if o.status != "reported"]
    run.statements_planned = len(written)
    run.statements_applied = sum(1 for o in written if o.status == "applied")
    run.statements_failed = sum(1 for o in written if o.status == "failed")
    run.removals_planned = sum(o.removals for o in written)
    run.skipped_catalogs = sorted(set(skipped_catalogs))
    run.paused_reason = paused_reason
    run.error = error
    counts = {"reported": len(outcomes) - len(written)} if len(written) < len(outcomes) else {}
    run.summary = {k: v for k, v in {**(run.summary or {}), **(summary or {}), **counts}.items() if k != _WORKER_KEY}
    run.finished_at = now
    db.commit()
    return _result(run, list(outcomes))


def record_run(
    db: Session,
    *,
    trigger: RunTrigger,
    mode: ReconcileMode,
    status: RunStatus,
    outcomes: Sequence[PlanOutcome] = (),
    actor: Optional[str] = None,
    **counters,
) -> ReconcileResult:
    """``start_run`` + ``finish_run`` for a run recorded in one go (skipped, paused, quick)."""
    run_id = start_run(db, trigger=trigger, mode=mode, actor=actor)
    return finish_run(db, run_id, status=status, outcomes=outcomes, **counters)


def list_runs(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    trigger: Optional[str] = None,
    status: Optional[str] = None,
) -> ReconcileRunPage:
    """Runs newest first, without their statements (``get_run`` has those)."""
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 50)), _MAX_PAGE_SIZE)
    query = db.query(MetadataReconcileRunModel)
    if trigger:
        query = query.filter(MetadataReconcileRunModel.trigger == trigger)
    if status:
        query = query.filter(MetadataReconcileRunModel.status == status)
    total = query.count()
    rows = (
        query.order_by(MetadataReconcileRunModel.started_at.desc(), MetadataReconcileRunModel.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ReconcileRunPage(items=[_result(r) for r in rows], total=total, page=page, page_size=page_size)


def get_run(db: Session, run_id: str) -> ReconcileResult:
    """Raises ``errors.NotFound``."""
    run = db.get(MetadataReconcileRunModel, run_id)
    if run is None:
        raise errors.NotFound(f"Reconcile run '{run_id}' not found.")
    rows = (
        db.query(MetadataReconcileItemModel)
        .filter(MetadataReconcileItemModel.run_id == run_id)
        .order_by(MetadataReconcileItemModel.id)
        .all()
    )
    return _result(run, _outcomes_from_rows(db, rows))


def recent_outcomes(db: Session, fqn_key: str, *, limit: int = 20) -> List[PlanOutcome]:
    """The latest statements recorded for an object and (for a relation) its columns, newest first."""
    pattern = fqn_key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + COLUMN_SEPARATOR + "%"
    rows = (
        db.query(MetadataReconcileItemModel)
        .filter(
            (MetadataReconcileItemModel.fqn_key == fqn_key)
            | MetadataReconcileItemModel.fqn_key.like(pattern, escape="\\")
        )
        .order_by(MetadataReconcileItemModel.id.desc())
        .limit(max(1, int(limit)))
        .all()
    )
    return _outcomes_from_rows(db, rows)


# --- Single flight and schedule ---------------------------------------------------------


class ReconcileBusy(errors.MetadataError):
    """Another reconcile run holds the lock (409)."""

    status_code = 409


@contextmanager
def _single_flight(db: Session) -> Iterator[None]:
    """One reconcile run at a time across replicas: a Postgres advisory lock (no-op on SQLite).

    Raises ``ReconcileBusy`` when another session holds it.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        yield
        return

    # A dedicated connection holds the session-level lock for the whole run; the
    # ORM session may hand its own connection back to the pool on each commit.
    engine = getattr(bind, "engine", bind)
    conn = engine.connect()
    try:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}).scalar()
        conn.commit()
        if not got:
            raise ReconcileBusy(
                "A check of Unity Catalog is already running. Wait for it to finish — it appears under Sync history."
            )
        try:
            yield
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
                conn.commit()
            except Exception:  # noqa: BLE001
                logger.warning("Couldn't release the reconcile lock; dropping its connection", exc_info=True)
                # Closing the server session releases the lock instead.
                conn.invalidate()
    finally:
        conn.close()


def _sweep_due(now: Optional[datetime] = None) -> bool:
    """True once per METADATA_RECONCILE_CRON tick (UTC); False when the cron is blank or invalid.

    The next tick is cached in ``_next_sweep_time``; settings_store clears it when the
    cron changes, and it's recomputed from the next call's ``now``.
    """
    global _next_sweep_time, _bad_cron
    cron_expr = str(getattr(settings, "METADATA_RECONCILE_CRON", "") or "").strip()
    if not cron_expr:
        return False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if _next_sweep_time is None:
        try:
            _next_sweep_time = croniter(cron_expr, now).get_next(datetime)
            _bad_cron = None
        except (CroniterBadCronError, ValueError, KeyError, TypeError):
            if _bad_cron != cron_expr:
                logger.error("Invalid METADATA_RECONCILE_CRON expression: %s", cron_expr)
                _bad_cron = cron_expr
            return False
    if now < _next_sweep_time:
        return False
    try:
        _next_sweep_time = croniter(cron_expr, now).get_next(datetime)
    except (CroniterBadCronError, ValueError, KeyError, TypeError):
        _next_sweep_time = None
    return True


def _poll_due() -> bool:
    """True once per METADATA_NEW_OBJECT_POLL_SECONDS (0 turns the poll off)."""
    global _last_poll_at
    interval = _int_setting("METADATA_NEW_OBJECT_POLL_SECONDS", 60)
    if interval <= 0:
        return False
    now = _monotonic()
    if _last_poll_at is not None and now - _last_poll_at < interval:
        return False
    _last_poll_at = now
    return True


# --- Infrastructure seams ------------------------------------------------------------------


def _session() -> Session:
    from app.db.session import get_lakebase_session

    return get_lakebase_session()


def _governance_provider():
    from app.core.workspaces import get_governance_uc_provider

    return get_governance_uc_provider()


def _alert_governance(subject: str, lines: Sequence[str]) -> None:
    """Email the governance group; failures are only logged. Called from the reconciler's worker thread."""
    to = str(getattr(settings, "GOVERNANCE_EMAIL_GROUP", "") or "").strip()
    if not to:
        logger.info("Metadata reconciler alert not emailed (GOVERNANCE_EMAIL_GROUP is blank): %s", subject)
        return
    body = "".join(f"<p>{html.escape(str(line))}</p>" for line in lines)
    try:
        from app.providers.notifications.client import NotificationProvider

        coro = NotificationProvider().send_email(to=to, subject=subject, body=body, is_html=True)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return
        coro.close()
        logger.warning("Metadata reconciler alert not emailed (called on the event loop): %s", subject)
    except Exception as e:  # noqa: BLE001
        logger.warning("Couldn't email the metadata reconciler alert '%s': %s", subject, e)


# --- Run --------------------------------------------------------------------------------


@dataclass
class _Work:
    """One managed row in a run: what was seen, what to run, and what it was before."""

    object_id: str
    fqn_key: str
    desired_hash: Optional[str]
    present: bool
    observed_at: Optional[datetime]
    drift_at: Optional[datetime]
    apply_status: Optional[str]
    apply_error: Optional[str]
    items: List[PlanItem] = field(default_factory=list)


@dataclass
class _Context:
    run_id: str
    trigger: str
    mode: str
    actor: Optional[str]
    provider: object
    override: bool
    now: datetime
    exempt: List[str]
    # Catalog -> why it was skipped (shown on the run). ``blocked`` ones had nothing planned at all.
    skipped: Dict[str, str] = field(default_factory=dict)
    blocked: Set[str] = field(default_factory=set)
    counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    timings: Dict[str, int] = field(default_factory=dict)
    # Queue keys to retry and why: their catalog couldn't be read, or adopting them failed.
    retry: Dict[str, str] = field(default_factory=dict)


@dataclass
class _RunReport:
    result: ReconcileResult
    retry: Dict[str, str] = field(default_factory=dict)
    # The run stopped before per-object outcomes were known.
    error: bool = False


def run(
    db: Session,
    *,
    trigger: RunTrigger,
    fqn_keys: Optional[List[str]] = None,
    catalogs: Optional[List[str]] = None,
    mode: Optional[ReconcileMode] = None,
    actor: Optional[str] = None,
    provider=None,
    override_circuit_breaker: bool = False,
) -> ReconcileResult:
    """Reconcile named objects (``fqn_keys``, filtered to ``catalogs`` if given) or whole catalogs (a sweep).

    A sweep covers ``catalogs``, default the scope (``observed.scope_catalogs``). ``mode``
    defaults to METADATA_RECONCILE_MODE; ``plan`` records statements without running
    any. In ``enforce``, a tripped breaker records a ``paused`` run unless overridden,
    and an overriding run clears it. Unmanaged keys named here are adopted under
    METADATA_NEW_OBJECT_POLICY=adopt. Raises ``ReconcileBusy``; any other failure is
    recorded as a ``failed`` run and returned.
    """
    return _run(
        db, trigger=trigger, fqn_keys=fqn_keys, catalogs=catalogs, mode=mode, actor=actor,
        provider=provider, override=override_circuit_breaker,
    ).result


def latest_running(db: Session, *, actor: Optional[str] = None) -> Optional[ReconcileResult]:
    """The newest still-running run, optionally for ``actor``. Closes abandoned rows first."""
    _close_abandoned_runs(db)
    query = db.query(MetadataReconcileRunModel).filter(MetadataReconcileRunModel.status == "running")
    if actor:
        query = query.filter(MetadataReconcileRunModel.actor == actor)
    row = query.order_by(MetadataReconcileRunModel.started_at.desc()).first()
    return _result(row) if row is not None else None


def run_detached(
    *,
    trigger: RunTrigger,
    fqn_keys: Optional[List[str]] = None,
    catalogs: Optional[List[str]] = None,
    mode: Optional[ReconcileMode] = None,
    actor: Optional[str] = None,
    override_circuit_breaker: bool = False,
    wait_seconds: float = _ACCEPT_SECONDS,
    session_factory: Optional[Callable[[], Session]] = None,
) -> ReconcileResult:
    """Start ``run`` on its own session and thread.

    If it finishes within ``wait_seconds``, return that result (empty catalogs, a
    lock conflict, a skipped mode). Otherwise return the still-running row so an
    HTTP caller can hang up; the thread keeps sweeping. A warehouse-sized catalog
    takes minutes and would otherwise time out the request and leave the UI stuck
    on Previewing.
    """
    from app.db.session import get_lakebase_session

    factory = session_factory or get_lakebase_session
    box: Dict[str, object] = {}

    def work() -> None:
        db = factory()
        try:
            box["result"] = run(
                db, trigger=trigger, fqn_keys=fqn_keys, catalogs=catalogs, mode=mode, actor=actor,
                override_circuit_breaker=override_circuit_breaker,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the waiter or logged
            box["error"] = exc
            if not isinstance(exc, ReconcileBusy):
                logger.error("Metadata detached reconcile failed: %s", exc, exc_info=True)
        finally:
            db.close()
            _live_runs.discard(threading.current_thread())

    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(work,), name="metadata-reconcile-run", daemon=True,
    )
    _live_runs.add(thread)
    thread.start()
    deadline = time.monotonic() + max(0.05, wait_seconds)
    while time.monotonic() < deadline:
        if "result" in box:
            return box["result"]  # type: ignore[return-value]
        err = box.get("error")
        if err is not None:
            raise err
        time.sleep(0.05)
        probe = factory()
        try:
            found = latest_running(probe, actor=actor)
            if found is not None:
                return found
        finally:
            probe.close()
    if "result" in box:
        return box["result"]  # type: ignore[return-value]
    err = box.get("error")
    if err is not None:
        raise err
    raise errors.MetadataError("The check didn’t start in time. Try again in a moment.")


def _run(
    db: Session,
    *,
    trigger: RunTrigger,
    fqn_keys: Optional[Sequence[str]],
    catalogs: Optional[Sequence[str]],
    mode: Optional[ReconcileMode],
    actor: Optional[str],
    provider,
    override: bool,
    adopt: Optional[Set[str]] = None,
) -> _RunReport:
    """``run``, plus what ``drain_queue`` needs; ``adopt`` limits adoption to those keys (None: all named)."""
    mode = mode or current_mode()
    if mode == "enforce" and not scope.is_explicit():
        mode = "plan"
    if mode == "off":
        return _RunReport(record_run(
            db, trigger=trigger, mode="off", status="skipped", actor=actor,
            paused_reason="Reconciliation is off (METADATA_RECONCILE_MODE).",
        ))
    with _single_flight(db):
        if mode == "enforce" and not override:
            breaker = circuit_breaker_state(db)
            if breaker is not None:
                return _RunReport(record_run(
                    db, trigger=trigger, mode=mode, status="paused", actor=actor, paused_reason=_paused_reason(breaker),
                ))
        if fqn_keys is not None:
            run_scope: dict = {"objects": len(fqn_keys)}
            if catalogs:
                run_scope["catalogs"] = sorted({c.strip().lower() for c in catalogs if c and c.strip()})
        else:
            run_scope = {"catalogs": sorted({c.strip().lower() for c in catalogs or [] if c and c.strip()}) or "scope"}
        run_id = start_run(db, trigger=trigger, mode=mode, actor=actor, summary={"scope": run_scope})
        # Every line the run logs, down to each warehouse read, carries req_id "metadata_reconcile:<run id>".
        with job_log_context("metadata_reconcile", run_id):
            logger.info(
                "Metadata reconcile %s run %s (%s) started by %s: %s.", trigger, run_id, mode, actor or "the app", run_scope,
            )
            ctx = _Context(
                run_id=run_id, trigger=trigger, mode=mode, actor=actor, provider=provider, override=override,
                now=_utcnow(), exempt=policy.exempt_keys(),
            )
            started = _monotonic()
            try:
                return _reconcile(db, ctx, fqn_keys=fqn_keys, catalogs=catalogs, adopt=adopt, started=started)
            except Exception as e:  # noqa: BLE001
                db.rollback()
                logger.error("Metadata reconcile %s run %s failed: %s", trigger, run_id, e, exc_info=True)
                ctx.timings["total_ms"] = _ms(started)
                result = finish_run(
                    db, run_id, status="failed", error=_clip(f"The run stopped: {e}"), skipped_catalogs=list(ctx.skipped),
                    summary={"skipped": dict(ctx.skipped), "unread": sorted(ctx.blocked), "timings_ms": dict(ctx.timings)},
                )
                return _RunReport(result, error=True)


def _reconcile(
    db: Session,
    ctx: _Context,
    *,
    fqn_keys: Optional[Sequence[str]],
    catalogs: Optional[Sequence[str]],
    adopt: Optional[Set[str]],
    started: float,
) -> _RunReport:
    if ctx.provider is None:
        ctx.provider = _governance_provider()
    if fqn_keys is not None:
        works = _targeted(db, ctx, _scope_keys(ctx, fqn_keys, catalogs), adopt)
    else:
        works = _sweep_scope(db, ctx, catalogs)
    # Snapshot and object-state writes land before any statement runs, so nothing stays locked meanwhile.
    db.commit()

    wanted = scope.enforce_patterns()
    reported_keys = {w.fqn_key for w in works if w.items and not scope.enforces(w.fqn_key, wanted)}
    items = _planned_items([w for w in works if w.fqn_key not in reported_keys])
    reported = [
        PlanOutcome(**i.model_dump(), status="reported", detail=_REPORTED_DETAIL)
        for i in _planned_items([w for w in works if w.fqn_key in reported_keys])
    ]
    removals = sum(i.removals for i in items)
    limit = _removal_limit()
    paused_reason: Optional[str] = None
    executed = False
    if ctx.mode == "enforce" and not ctx.override and removals > limit:
        breaker = trip_circuit_breaker(db, run_id=ctx.run_id, removals=removals, limit=limit)
        paused_reason = _paused_reason(breaker)
        outcomes = [_planned(i) for i in items]
    elif ctx.mode == "enforce" and items:
        applying = _monotonic()
        outcomes = apply_.execute(ctx.provider, items, workers=_apply_workers())
        ctx.timings["apply_ms"] = _ms(applying)
        executed = True
        _patch_snapshot(db, outcomes)
    else:
        outcomes = [_planned(i) for i in items]
    if ctx.mode == "enforce" and ctx.override and paused_reason is None:
        if clear_circuit_breaker(db, cleared_by=ctx.actor or "override"):
            logger.info("Metadata reconcile run %s cleared the circuit breaker (override).", ctx.run_id)
    outcomes = list(outcomes) + reported

    _record_objects(db, works, outcomes, executed=executed, now=ctx.now, reported=reported_keys)
    ctx.timings["total_ms"] = _ms(started)
    result = finish_run(
        db, ctx.run_id,
        status="paused" if paused_reason else run_status(outcomes, mode=ctx.mode),
        outcomes=outcomes,
        objects_checked=sum(1 for w in works if w.present),
        objects_drifted=sum(1 for w in works if w.items),
        skipped_catalogs=list(ctx.skipped),
        paused_reason=paused_reason,
        summary={
            "counts": dict(ctx.counts), "skipped": dict(ctx.skipped), "unread": sorted(ctx.blocked),
            "timings_ms": dict(ctx.timings),
        },
    )
    _log_run(result, ctx)
    if paused_reason:
        _report_breaker_trip(ctx, removals, limit)
    if ctx.trigger == "sweep":
        _purge_orphans(db)
    return _RunReport(result, retry=dict(ctx.retry))


def _planned(item: PlanItem) -> PlanOutcome:
    return PlanOutcome(**item.model_dump(), status="planned")


def _scope_keys(ctx: _Context, fqn_keys: Sequence[str], catalogs: Optional[Sequence[str]]) -> List[str]:
    """Relation-level queue keys, deduplicated; invalid keys and catalogs this deployment doesn't manage are dropped."""
    allowed = {c.strip().lower() for c in catalogs or [] if c and c.strip()}
    keys: Set[str] = set()
    for fqn_key in fqn_keys:
        try:
            key = queue_key(fqn_key)
        except (ValueError, errors.MetadataError) as e:
            logger.warning("Metadata reconcile: ignoring invalid object key '%s': %s", fqn_key, e)
            continue
        catalog = _catalog_of(key)
        if not scope.manages(catalog):
            ctx.skipped[catalog] = _OUTSIDE_SCOPE
            continue
        if not allowed or catalog in allowed:
            keys.add(key)
    if ctx.skipped:
        logger.info("Metadata reconcile run %s: ignored objects in catalogs outside METADATA_RECONCILE_CATALOGS: %s",
                    ctx.run_id, ", ".join(sorted(ctx.skipped)))
    return sorted(keys)


def _targeted(db: Session, ctx: _Context, keys: List[str], adopt: Optional[Set[str]]) -> List[_Work]:
    """Named relations (with their columns): one bulk refresh, then plan."""
    from app.services.metadata import store

    if not keys:
        return []
    observing = _monotonic()
    requested = [ObjectKey.from_fqn_key(k) for k in keys]
    seen = snapshot.refresh(db, requested, provider=ctx.provider)
    db.commit()
    ctx.timings["observe_ms"] = _ms(observing)

    unreadable = sorted({k.catalog.lower() for k in requested if k.fqn_key not in seen})
    for catalog in unreadable:
        ctx.skipped[catalog] = "Couldn't read it from Unity Catalog; its objects will be retried."
        ctx.blocked.add(catalog)
    for key in keys:
        if _catalog_of(key) in ctx.blocked:
            ctx.retry[key] = ctx.skipped[_catalog_of(key)]

    relations = [k for k in keys if _level_type(k) == "table"]
    desired = store.get_objects(db, keys + _desired_column_keys(db, relations))
    unmanaged = [k for k in keys if k not in desired and (adopt is None or k in adopt)]
    _adopt(db, ctx, unmanaged, seen)
    return _plan_objects(db, ctx, desired, seen)


def _desired_column_keys(db: Session, relation_keys: Sequence[str]) -> List[str]:
    """Active desired column rows of these relations (store.get_columns, in bulk)."""
    found: List[str] = []
    relations = sorted(set(relation_keys))
    for start in range(0, len(relations), _IN_CHUNK):
        chunk = relations[start:start + _IN_CHUNK]
        found.extend(db.execute(
            select(_OBJECTS.c.fqn_key).where(
                _OBJECTS.c.parent_key.in_(chunk),
                _OBJECTS.c.securable_type == "column",
                _OBJECTS.c.status == OBJECT_ACTIVE,
            )
        ).scalars())
    return found


def _desired_keys_in(db: Session, catalogs: Sequence[str]) -> List[str]:
    """Every active desired row in these catalogs, ordered so a relation's columns follow it."""
    if not catalogs:
        return []
    return list(db.execute(
        select(_OBJECTS.c.fqn_key)
        .where(func.lower(_OBJECTS.c.catalog).in_(list(catalogs)), _OBJECTS.c.status == OBJECT_ACTIVE)
        .order_by(_OBJECTS.c.fqn_key)
    ).scalars())


def _relation_chunks(keys: Sequence[str], size: int) -> Iterator[List[str]]:
    groups: Dict[str, List[str]] = {}
    for key in keys:
        groups.setdefault(_relation_of(key), []).append(key)
    chunk: List[str] = []
    for members in groups.values():
        chunk.extend(members)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _sweep_scope(db: Session, ctx: _Context, catalogs: Optional[Sequence[str]]) -> List[_Work]:
    """Whole catalogs: sweep the snapshot, then plan every managed row in the readable ones."""
    from app.services.metadata import store

    observing = _monotonic()
    wanted = list(dict.fromkeys(c.strip().lower() for c in catalogs or [] if c and c.strip()))
    targets = wanted or snapshot.scope_catalogs(ctx.provider)
    for catalog in targets:
        if not scope.manages(catalog):
            ctx.skipped[catalog] = _OUTSIDE_SCOPE
    targets = [c for c in targets if scope.manages(c)]
    if not targets:
        return []
    swept = snapshot.sweep(db, provider=ctx.provider, catalogs=targets)
    ctx.timings["observe_ms"] = _ms(observing)
    for catalog, error in swept.failed_catalogs.items():
        ctx.skipped[catalog] = _clip(f"Couldn't read it from Unity Catalog: {error}", 500)
        ctx.blocked.add(catalog)
    readable = [c for c in targets if c not in ctx.blocked]

    works: List[_Work] = []
    for chunk in _relation_chunks(_desired_keys_in(db, [c for c in readable if c not in ctx.blocked]), _SWEEP_CHUNK):
        desired = store.get_objects(db, chunk)
        parents = {_relation_of(k) for k in chunk if COLUMN_SEPARATOR in k}
        works.extend(_plan_objects(db, ctx, desired, snapshot.get_observed(db, set(chunk) | parents)))
        db.commit()
    return works


def _adopt(db: Session, ctx: _Context, keys: Sequence[str], seen: Dict[str, ObservedState]) -> None:
    """METADATA_NEW_OBJECT_POLICY=adopt: unmanaged objects that exist get desired state = observed.

    Only with an explicit METADATA_RECONCILE_CATALOGS: a deployment that manages no
    catalogs in particular doesn't take over new tables on a shared metastore.
    """
    if not keys or not _adopt_new_objects() or not scope.is_explicit():
        return
    candidates = [
        k for k in keys
        if _catalog_of(k) not in ctx.skipped and seen.get(k) is not None and seen[k].present
    ]
    if not candidates:
        return
    from app.services.metadata import changes

    try:
        changes.adopt_observed(db, candidates, reason=_ADOPT_REASON)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning("Metadata reconcile: couldn't adopt %d new object(s): %s", len(candidates), e, exc_info=True)
        for key in candidates:
            ctx.retry[key] = _clip(f"Couldn't adopt it: {e}")
        return
    ctx.counts["adopted"] += len(candidates)


def _seen_type(desired: DesiredState, observed: ObservedState) -> Optional[str]:
    """UC's relation type when it differs from the desired row's (a view created as pending 'table')."""
    if observed.securable_type in RELATION_TYPES and observed.securable_type != desired.securable_type:
        return observed.securable_type
    return None


def _plan_objects(
    db: Session, ctx: _Context, desired: Dict[str, DesiredState], seen: Dict[str, ObservedState],
) -> List[_Work]:
    """Lifecycle (pending/orphaned -> exists, exists -> orphaned), then each present row's statements."""
    from app.services.metadata import store

    works: List[_Work] = []
    for fqn_key in sorted(desired):
        catalog = _catalog_of(fqn_key)
        if catalog in ctx.blocked:
            continue
        row = desired[fqn_key]
        obs = seen.get(fqn_key)
        present = obs is not None and obs.present
        if present and row.object_state != "exists":
            store.set_object_state(db, fqn_key, "exists", securable_type=_seen_type(row, obs))
            ctx.counts["now_exist"] += 1
        elif not present and row.object_state == "exists":
            store.set_object_state(db, fqn_key, "orphaned")
            ctx.counts["orphaned"] += 1
        work = _Work(
            object_id=row.id, fqn_key=fqn_key, desired_hash=row.desired_hash, present=present,
            observed_at=obs.observed_at if present else None, drift_at=row.drift_detected_at,
            apply_status=row.last_apply_status, apply_error=row.last_apply_error,
        )
        if present:
            object_diff = diff_.diff(row, obs, exempt_keys=ctx.exempt)
            if not object_diff.in_sync:
                parent = seen.get(_relation_of(fqn_key)) if COLUMN_SEPARATOR in fqn_key else None
                work.items = diff_.plan(object_diff, observed=obs, object_id=row.id, parent=parent)
        works.append(work)
    return works


def _planned_items(works: Sequence[_Work]) -> List[PlanItem]:
    return sorted((i for w in works for i in w.items), key=diff_.plan_sort_key)


def _patch_snapshot(db: Session, outcomes: Sequence[PlanOutcome]) -> None:
    """Fold applied statements into the snapshot so the drawer and the next diff see them before the next sweep."""
    by_key: Dict[str, List[PlanOutcome]] = defaultdict(list)
    for outcome in outcomes:
        if outcome.status in ("applied", "noop") and outcome.sql:
            by_key[outcome.fqn_key].append(outcome)
    keys = sorted(by_key)
    for start in range(0, len(keys), _IN_CHUNK):
        chunk = keys[start:start + _IN_CHUNK]
        for row in db.query(MetadataObservedModel).filter(MetadataObservedModel.fqn_key.in_(chunk)):
            tags = dict(row.tags or {})
            comment = row.comment
            for outcome in by_key[row.fqn_key]:
                if outcome.operation == "set":
                    tags.update({k: normalize_value(v) for k, v in outcome.tags.items()})
                elif outcome.operation == "unset":
                    for key in outcome.keys:
                        tags.pop(key, None)
                elif outcome.operation == "comment":
                    comment = normalize_value(outcome.comment)
            row.tags = tags
            row.comment = comment
    db.flush()


def _record_objects(
    db: Session, works: Sequence[_Work], outcomes: Sequence[PlanOutcome], *, executed: bool, now: datetime,
    reported: Set[str] = frozenset(),
) -> None:
    """Each row's apply status and drift, written in bulk and only where something changed.

    Guarded by ``desired_hash``: a row edited while the run worked keeps its state
    (its change re-enqueued it, and the next run records it). ``reported`` objects
    weren't written even when the run executed, so their drift stands as in plan mode.
    """
    failures: Dict[str, str] = {}
    for outcome in outcomes:
        if outcome.status in ("failed", "skipped"):
            failures.setdefault(outcome.fqn_key, outcome.detail or f"The {outcome.operation} statement didn't run.")
    converged: List[dict] = []
    failed: List[dict] = []
    drifted: List[dict] = []
    cleared: List[dict] = []
    for work in works:
        guard = {"b_id": work.object_id, "b_hash": work.desired_hash}
        if not work.present:
            if work.drift_at is not None:
                cleared.append(guard)
        elif work.fqn_key in failures:
            failed.append({**guard, "b_error": _clip(failures[work.fqn_key])})
        elif work.items and executed and work.fqn_key not in reported:
            converged.append({**guard, "b_applied": now, "b_seen": work.observed_at})
        elif work.items:
            if work.drift_at is None:
                drifted.append(guard)
        elif work.apply_status != "applied" or work.apply_error or work.drift_at is not None:
            converged.append({**guard, "b_applied": None, "b_seen": work.observed_at})
    same_row = and_(
        _OBJECTS.c.id == bindparam("b_id"),
        _OBJECTS.c.desired_hash.is_not_distinct_from(bindparam("b_hash")),
    )
    statements = (
        (converged, update(_OBJECTS).where(same_row).values(
            last_applied_at=func.coalesce(bindparam("b_applied", type_=_OBJECTS.c.last_applied_at.type),
                                          _OBJECTS.c.last_applied_at),
            last_apply_status="applied", last_apply_error=None, drift_detected_at=None,
            last_observed_at=bindparam("b_seen", type_=_OBJECTS.c.last_observed_at.type),
        )),
        (failed, update(_OBJECTS).where(same_row).values(
            last_apply_status="failed", last_apply_error=bindparam("b_error"),
            drift_detected_at=func.coalesce(_OBJECTS.c.drift_detected_at, now),
        )),
        (drifted, update(_OBJECTS).where(same_row).values(drift_detected_at=now)),
        (cleared, update(_OBJECTS).where(same_row).values(drift_detected_at=None)),
    )
    for params, statement in statements:
        for start in range(0, len(params), _WRITE_CHUNK):
            db.execute(statement, params[start:start + _WRITE_CHUNK])


def _log_run(result: ReconcileResult, ctx: _Context) -> None:
    counts = ctx.counts
    logger.info(
        "Metadata reconcile %s run %s (%s): %s. %d object(s) checked, %d drifted; %d statement(s), %d applied, "
        "%d failed, %d tag removal(s), %d reported only (outside the enforced schemas); %d adopted, %d now exist, "
        "%d orphaned; %d catalog(s) skipped. Observe %s ms, apply %s ms, total %s ms.",
        result.trigger, result.run_id, result.mode, result.status, result.objects_checked, result.objects_drifted,
        result.statements_planned, result.statements_applied, result.statements_failed, result.removals_planned,
        result.statements_reported,
        counts.get("adopted", 0), counts.get("now_exist", 0), counts.get("orphaned", 0), len(result.skipped_catalogs),
        ctx.timings.get("observe_ms", 0), ctx.timings.get("apply_ms", 0), ctx.timings.get("total_ms", 0),
    )


def _report_breaker_trip(ctx: _Context, removals: int, limit: int) -> None:
    logger.warning(
        "Metadata reconcile run %s tripped the circuit breaker: it would remove %d tags or descriptions (limit %d). "
        "Nothing was applied; enforcement is paused until an override run.", ctx.run_id, removals, limit,
    )
    _alert_governance(
        "[Action required] Metadata reconciler paused: too many removals",
        [
            f"A {ctx.trigger} run would remove {removals} tags or descriptions from Unity Catalog; the limit is {limit} "
            "(METADATA_MAX_REMOVALS_PER_RUN).",
            "Nothing was applied. Runs that change Unity Catalog stay paused until an admin reviews the run "
            "on the Runs tab and runs again with the circuit-breaker override.",
            f"Run: {ctx.run_id}",
        ],
    )


def _purge_orphans(db: Session) -> None:
    from app.services.metadata import store

    days = _int_setting("METADATA_ORPHAN_RETENTION_DAYS", 30)
    if days < 1:
        return
    try:
        purged = store.purge_orphans(db, older_than_days=days)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning("Metadata reconcile: purging orphaned objects failed: %s", e, exc_info=True)
        return
    if purged:
        logger.info("Metadata reconcile purged %d object(s) orphaned for more than %d days.", purged, days)


# --- Queue ------------------------------------------------------------------------------


def _backoff_seconds(attempts: int) -> int:
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * 2 ** min(max(attempts, 1) - 1, 16))


def _queue_trigger(reasons: Iterable[str]) -> RunTrigger:
    """What Sync history says started a queue run: an edit here, a change found in UC, or a notify call."""
    found = set(reasons)
    if not found or "change" in found or not found <= {"new_object", "notify"}:
        return "queue"
    return "notify" if found == {"notify"} else "new_object"


def drain_queue(db: Session, *, provider=None, limit: int = _BATCH_SIZE) -> Optional[ReconcileResult]:
    """Run the oldest due queue rows (up to ``limit``) as one ``queue`` run; None when nothing is due.

    Rows whose objects converged (or were planned, in plan mode) are deleted. The rest
    are retried with exponential backoff (30 s, doubling, capped at an hour); a row
    still failing after five attempts is logged and reported to governance. A paused
    or skipped run leaves the rows as they are, and a row re-enqueued while the run
    worked stays queued for the next drain. Raises ``ReconcileBusy``.
    """
    started = _monotonic()
    rows = db.execute(
        select(_QUEUE.c.id, _QUEUE.c.fqn_key, _QUEUE.c.reason, _QUEUE.c.attempts, _QUEUE.c.not_before)
        .where(_QUEUE.c.not_before <= _utcnow())
        .order_by(_QUEUE.c.not_before, _QUEUE.c.id)
        .limit(max(1, int(limit or _BATCH_SIZE)))
    ).all()
    if not rows:
        db.commit()
        return None
    report = _run(
        db, trigger=_queue_trigger(r.reason for r in rows), fqn_keys=[r.fqn_key for r in rows], catalogs=None,
        mode=None, actor=None,
        provider=provider, override=False, adopt={r.fqn_key for r in rows if r.reason in _ADOPT_REASONS},
    )
    done, retrying, exhausted = _settle(db, rows, report)
    remaining = db.execute(select(func.count()).select_from(_QUEUE)).scalar() or 0
    db.commit()
    logger.info(
        "Metadata reconcile queue: batch of %d (%s): %d done, %d to retry, %d left in the queue (%d ms).",
        len(rows), report.result.status, done, retrying, remaining, _ms(started),
    )
    if exhausted:
        _report_exhausted(exhausted)
    return report.result


def _settle(db: Session, rows: Sequence, report: _RunReport) -> Tuple[int, int, List[Tuple[str, str]]]:
    """Delete converged rows and back off the rest; returns (done, retrying, rows that just hit the attempt limit)."""
    result = report.result
    if result.status in ("paused", "skipped"):
        return 0, 0, []
    if report.error:
        failures = {r.fqn_key: result.error or "The run failed." for r in rows}
    else:
        failures = dict(report.retry)
        for outcome in result.items:
            if outcome.status in ("failed", "skipped"):
                failures.setdefault(
                    queue_key(outcome.fqn_key), outcome.detail or f"The {outcome.operation} statement didn't run.",
                )
    now = _utcnow()
    deletes: List[dict] = []
    backoffs: List[dict] = []
    exhausted: List[Tuple[str, str]] = []
    for row in rows:
        error = failures.get(row.fqn_key)
        if error is None:
            deletes.append({"b_id": row.id, "b_before": row.not_before})
            continue
        attempts = (row.attempts or 0) + 1
        backoffs.append({
            "b_id": row.id, "b_before": row.not_before, "b_attempts": attempts, "b_error": _clip(error),
            "b_next": now + timedelta(seconds=_backoff_seconds(attempts)),
        })
        if attempts == _MAX_ATTEMPTS:
            exhausted.append((row.fqn_key, error))
    # A row re-enqueued during the run has a new not_before: leave it for the next drain.
    unchanged = and_(
        _QUEUE.c.id == bindparam("b_id"),
        _QUEUE.c.not_before == bindparam("b_before", type_=_QUEUE.c.not_before.type),
    )
    if deletes:
        db.execute(delete(_QUEUE).where(unchanged), deletes)
    if backoffs:
        db.execute(
            update(_QUEUE).where(unchanged).values(
                attempts=bindparam("b_attempts"), last_error=bindparam("b_error"),
                not_before=bindparam("b_next", type_=_QUEUE.c.not_before.type),
            ),
            backoffs,
        )
    db.commit()
    return len(deletes), len(backoffs), exhausted


def _report_exhausted(exhausted: Sequence[Tuple[str, str]]) -> None:
    shown = list(exhausted[:50])
    logger.warning(
        "Metadata reconcile: %d queued object(s) failed %d times in a row and are now retried hourly: %s",
        len(exhausted), _MAX_ATTEMPTS, ", ".join(k for k, _ in shown[:20]),
    )
    lines = [f"These objects failed to reconcile {_MAX_ATTEMPTS} times in a row; they're now retried hourly:"]
    lines += [f"{key}: {error}" for key, error in shown]
    if len(exhausted) > len(shown):
        lines.append(f"...and {len(exhausted) - len(shown)} more.")
    _alert_governance("Metadata reconciler: objects keep failing to reconcile", lines)


# --- Poller entry point -------------------------------------------------------------------


async def reconcile_task() -> None:
    """Poller entry point (leader only). Never raises.

    The pass runs in a worker thread so the poller's event loop keeps serving its other tasks.
    """
    if current_mode() == "off":
        return
    try:
        await asyncio.to_thread(_reconcile_pass)
    except Exception as e:  # noqa: BLE001
        _note_pass_error(f"Metadata reconcile pass failed: {e}")


def _note_pass_error(message: str) -> None:
    """Log a pass failure, once until it changes or a pass succeeds."""
    global _pass_error_noted
    if message != _pass_error_noted:
        logger.error(message, exc_info=True)
        _pass_error_noted = message


def _queue_due(db: Session) -> bool:
    return db.execute(select(_QUEUE.c.id).where(_QUEUE.c.not_before <= _utcnow()).limit(1)).first() is not None


def _reconcile_pass(*, budget_seconds: float = _PASS_BUDGET_SECONDS) -> None:
    """One poller cycle: the new-object poll when due, drain batches until the budget runs out, then a due sweep."""
    global _pass_error_noted
    deadline = _monotonic() + budget_seconds
    db = _session()
    try:
        poll, sweep = _poll_due(), _sweep_due()
        if not (poll or sweep or _queue_due(db)):
            return
        try:
            provider = _governance_provider()
        except Exception as e:  # noqa: BLE001
            _note_pass_error(f"Metadata reconciler couldn't connect to Unity Catalog as the governance principal: {e}")
            return
        if poll:
            try:
                _poll_new_objects(db, provider)
            except Exception as e:  # noqa: BLE001
                db.rollback()
                _note_pass_error(f"Metadata new-object poll failed: {e}")
        _drain_until(db, provider, deadline)
        if sweep:
            try:
                run(db, trigger="sweep", provider=provider)
            except ReconcileBusy:
                logger.info("Metadata reconcile sweep skipped: another run is in progress.")
        _pass_error_noted = None
    finally:
        db.close()


def _drain_until(db: Session, provider, deadline: float) -> None:
    """Drain batches while work is due and the pass has time; stop on a paused, skipped or failed batch."""
    if current_mode() == "enforce" and _breaker_blocks(db):
        return
    while _monotonic() < deadline:
        try:
            result = drain_queue(db, provider=provider)
        except ReconcileBusy:
            logger.debug("Metadata reconcile queue: another run holds the lock; draining next cycle.")
            return
        if result is None or result.status in ("paused", "skipped") or result.error:
            return


def _breaker_blocks(db: Session) -> bool:
    """While tripped, enforce-mode drains wait (logged once per trip) rather than recording a paused run per cycle."""
    global _breaker_noted
    breaker = circuit_breaker_state(db)
    if breaker is None:
        _breaker_noted = None
        return False
    tripped_at = str(breaker.get("tripped_at"))
    if tripped_at != _breaker_noted:
        logger.warning(
            "Metadata reconcile queue paused: the circuit breaker tripped at %s (%s). Waiting for an override run.",
            tripped_at, breaker.get("reason"),
        )
        _breaker_noted = tripped_at
    return True


def _watermark(db: Session) -> Optional[datetime]:
    value = _get_state(db, snapshot.NEW_OBJECT_WATERMARK_KEY)
    at = value.get("at") if isinstance(value, dict) else None
    if not at:
        return None
    try:
        return datetime.fromisoformat(str(at)).replace(tzinfo=None)
    except ValueError:
        return None


def _poll_new_objects(db: Session, provider) -> List[str]:
    """Find relations created or altered since the last poll and queue the ones that need a run."""
    started = _monotonic()
    since = _watermark(db)
    found = snapshot.poll_new_objects(db, provider=provider)
    db.commit()
    if not found:
        return []
    keys = _needing_reconcile(db, found, since)
    if keys:
        enqueue(db, keys, reason="new_object")
        db.commit()
    logger.info(
        "Metadata new-object poll: %d relation(s) created or altered, %d queued (%d ms).",
        len(found), len(keys), _ms(started),
    )
    return keys


def _needing_reconcile(db: Session, found: Sequence[str], since: Optional[datetime]) -> List[str]:
    """Of the polled relations: managed ones out of sync or changing lifecycle, and (to adopt) unmanaged new ones.

    Our own statements bump ``last_altered``, so in-sync managed relations are dropped
    here, and an unmanaged relation must be created after the previous watermark.
    """
    from app.services.metadata import store

    keys = sorted({queue_key(k) for k in found})
    desired = store.get_objects(db, keys + _desired_column_keys(db, keys))
    seen = snapshot.get_observed(db, set(keys) | set(desired))
    exempt = policy.exempt_keys()
    need: Set[str] = set()
    for fqn_key, row in desired.items():
        obs = seen.get(fqn_key)
        present = obs is not None and obs.present
        if (row.object_state == "exists") != present:
            need.add(queue_key(fqn_key))
        elif present and not diff_.diff(row, obs, exempt_keys=exempt).in_sync:
            need.add(queue_key(fqn_key))
    if _adopt_new_objects():
        managed = {queue_key(k) for k in desired}
        for key in keys:
            obs = seen.get(key)
            if key in managed or obs is None or not obs.present:
                continue
            if since is None or (obs.created is not None and obs.created > since):
                need.add(key)
    return sorted(need)
