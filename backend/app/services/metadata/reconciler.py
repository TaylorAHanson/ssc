"""The reconciler: queue, runs, modes and circuit breaker (ADR-0006 D3). Workstream C.

``run`` reconciles a scope: named objects (a queue batch) or whole catalogs (a sweep).
It refreshes the snapshot for that scope, diffs every managed row against UC under
full enforcement, then records the plan (``plan``) or applies it and records the
outcome (``enforce``). Catalogs outside
METADATA_RECONCILE_CATALOGS (``scope``) are never touched, and enforce needs that list
to be set. A run that would take removals (tags or descriptions removed or blanked)
over METADATA_MAX_REMOVALS_PER_RUN within the last hour trips the circuit breaker
and applies nothing.

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
from sqlalchemy import and_, bindparam, case, delete, event, func, insert, or_, select, text, update
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
# Removals count against METADATA_MAX_REMOVALS_PER_RUN together across this window, so
# queue batches and back-to-back runs can't each stay under the limit.
REMOVAL_WINDOW = timedelta(hours=1)
DEFAULT_REMOVAL_LIMIT = 50
# Session-level Postgres advisory lock key that keeps reconcile runs single flight across replicas.
_ADVISORY_LOCK_KEY = 5_006_202_610_200_001
# True while this context holds that lock; start_run then knows no other run can be live.
_holding_run_lock: contextvars.ContextVar[bool] = contextvars.ContextVar("metadata_holding_run_lock", default=False)
# While a run holds the lock: whether its own connection still does (Postgres only).
_lock_check: contextvars.ContextVar[Optional[Callable[[], bool]]] = contextvars.ContextVar(
    "metadata_lock_check", default=None,
)
# Set on app shutdown: a writing run stops between relations instead of being cut off mid-statement.
_shutdown = threading.Event()
# How often, at most, a writing run re-checks its lock and the settings.
_STOP_CHECK_SECONDS = 5.0
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
# Per statement of a run against the app database (the warehouse reads have their own limits).
# Every statement is chunked, so the slowest takes seconds; these only end a stuck one.
_STATEMENT_TIMEOUT_MS = 15 * 60 * 1000
_LOCK_TIMEOUT_MS = 60 * 1000
# Sync history older than METADATA_RUN_HISTORY_DAYS is deleted this many runs at a time.
_PRUNE_BATCH = 200
# Queue reasons whose unmanaged objects the new-object policy adopts. Other reasons
# (a change, a manual run) name objects that should already have desired state.
_ADOPT_REASONS = ("new_object", "notify")
_ADOPT_REASON = "New in Unity Catalog with no desired state; adopted as observed (METADATA_NEW_OBJECT_POLICY=adopt)."
_MERGE_REASON = (
    "Created or recreated in Unity Catalog under existing desired state; kept the tags and description it was "
    "created with that the desired state doesn't set."
)
# UC created times that differ by less than this are the same object (stored precision varies).
_CREATED_SLACK = timedelta(seconds=1)
# Queue reasons that only report a change seen in Unity Catalog. Re-queueing a row for one keeps its
# attempt count and backoff, so an object something else keeps changing still reaches the alert.
_OBSERVED_REASONS = ("new_object",)
# A queue row for a change seen in UC whose object was written in this many runs within the window
# is backed off rather than done: something keeps changing it back, or UC stores it differently.
_REWRITE_LIMIT = 3
_REWRITE_WINDOW = timedelta(hours=1)

_QUEUE = MetadataReconcileQueueModel.__table__
_OBJECTS = MetadataObjectModel.__table__
_ITEMS = MetadataReconcileItemModel.__table__
_RUNS = MetadataReconcileRunModel.__table__


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
    not_before: Optional[datetime] = None,
) -> List[str]:
    """Add objects to the queue (deduplicated per relation), due at ``not_before`` (default now).
    Doesn't commit: callers enqueue inside their transaction.

    Re-enqueueing a row that was backing off makes it due then with a fresh attempt
    count, except for a change seen in Unity Catalog (``new_object``), which keeps
    both and never makes a row due sooner. One upsert, so two edits queueing the same
    new object at once don't collide.
    """
    keys = sorted({queue_key(k) for k in fqn_keys if k})
    if not keys:
        return []
    enqueued_at = _utcnow()
    now = max(not_before, enqueued_at) if not_before is not None else enqueued_at
    dialect = db.get_bind().dialect.name
    if dialect in ("postgresql", "sqlite"):
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as upsert
        else:
            from sqlalchemy.dialects.sqlite import insert as upsert
        stmt = upsert(_QUEUE)
        new = stmt.excluded
        updates = {
            "reason": new.reason,
            "requested_by": func.coalesce(new.requested_by, _QUEUE.c.requested_by),
            "change_set_id": func.coalesce(new.change_set_id, _QUEUE.c.change_set_id),
        }
        if reason in _OBSERVED_REASONS:
            updates["not_before"] = case((_QUEUE.c.not_before > new.not_before, _QUEUE.c.not_before), else_=new.not_before)
        else:
            updates.update(not_before=new.not_before, attempts=0, last_error=None)
        stmt = stmt.on_conflict_do_update(index_elements=[_QUEUE.c.fqn_key], set_=updates)
        rows = [{
            "fqn_key": key, "reason": reason, "requested_by": requested_by, "change_set_id": change_set_id,
            "attempts": 0, "last_error": None, "enqueued_at": enqueued_at, "not_before": now,
        } for key in keys]
        for start in range(0, len(rows), _WRITE_CHUNK):
            db.execute(stmt, rows[start:start + _WRITE_CHUNK])
        return keys
    existing: Dict[str, MetadataReconcileQueueModel] = {}
    for start in range(0, len(keys), _IN_CHUNK):
        chunk = keys[start:start + _IN_CHUNK]
        for row in db.query(MetadataReconcileQueueModel).filter(MetadataReconcileQueueModel.fqn_key.in_(chunk)):
            existing[row.fqn_key] = row
    for key in keys:
        row = existing.get(key)
        if row is None:
            db.add(MetadataReconcileQueueModel(
                fqn_key=key, reason=reason, requested_by=requested_by, change_set_id=change_set_id,
                enqueued_at=enqueued_at, not_before=now,
            ))
        else:
            row.reason = reason
            row.requested_by = requested_by or row.requested_by
            row.change_set_id = change_set_id or row.change_set_id
            if reason in _OBSERVED_REASONS:
                row.not_before = max(row.not_before or now, now)
            else:
                row.not_before, row.attempts, row.last_error = now, 0, None
    return keys


def current_mode() -> ReconcileMode:
    """METADATA_RECONCILE_MODE; blank or unknown is ``plan`` (writes nothing), and so is enforce with no
    catalog list or no schema list."""
    global _bad_mode
    raw = str(getattr(settings, "METADATA_RECONCILE_MODE", "") or "").strip().lower()
    if raw == "enforce" and (not scope.is_explicit() or not scope.enforce_patterns()):
        if _bad_mode != "enforce":
            blank = "METADATA_RECONCILE_CATALOGS" if not scope.is_explicit() else "METADATA_ENFORCE_SCHEMAS"
            logger.warning("METADATA_RECONCILE_MODE is enforce but %s is blank; reconciling in plan mode (no writes).",
                           blank)
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
    return max(0, _int_setting("METADATA_MAX_REMOVALS_PER_RUN", DEFAULT_REMOVAL_LIMIT))


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
    db: Session, *, run_id: str, removals: int, limit: int, reason: Optional[str] = None, recent: int = 0,
) -> dict:
    """Record that a run was paused for too many removals. Doesn't commit."""
    if reason is None:
        reason = f"The run would remove {removals} tags or descriptions; the limit is {limit} an hour."
        if recent:
            reason = (f"The run would remove {removals} tags or descriptions, and {recent} were already removed in "
                      f"the last hour; the limit is {limit} an hour.")
    value = {
        "tripped": True,
        "run_id": run_id,
        "removals": int(removals),
        "recent_removals": int(recent),
        "limit": int(limit),
        "reason": reason,
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


def _parse_time(value: object) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed else None


def recent_removals(db: Session, *, now: Optional[datetime] = None) -> int:
    """Tags and descriptions enforce removed or blanked in the last REMOVAL_WINDOW.

    Counting restarts when an admin clears the breaker, and overriding runs (reviewed
    removals) aren't counted.
    """
    since = (now or _utcnow()) - REMOVAL_WINDOW
    cleared = _parse_time((_get_state(db, CIRCUIT_BREAKER_KEY) or {}).get("cleared_at"))
    if cleared is not None and cleared > since:
        since = cleared
    Run = MetadataReconcileRunModel
    rows = (
        db.query(Run.summary)
        .filter(Run.mode == "enforce", Run.started_at >= since - _ABANDONED_RUN_AGE, Run.finished_at >= since)
        .all()
    )
    return sum(int((s or {}).get("removals_applied") or 0) for (s,) in rows if not (s or {}).get("override"))


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
_LOST_LOCK_ERROR = (
    "This run lost its database lock while working, so it was marked stopped and another run could start. "
    "It stopped writing when it noticed; what it did is listed below."
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


# A bigint advisory key shows in pg_locks as classid (high 32 bits), objid (low 32 bits), objsubid 1.
_LOCK_ROW = (
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted AND objsubid = 1"
    " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
    " AND classid::bigint = :hi AND objid::bigint = :lo{extra})"
)
_LOCK_PARAMS = {"hi": (_ADVISORY_LOCK_KEY >> 32) & 0xFFFFFFFF, "lo": _ADVISORY_LOCK_KEY & 0xFFFFFFFF}


def _run_lock_held(db: Session) -> Optional[bool]:
    """Whether any session holds the reconcile advisory lock; ``None`` when that can't be told (SQLite, an error)."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return None
    engine = getattr(bind, "engine", bind)
    try:
        with engine.connect() as conn:
            return bool(conn.execute(text(_LOCK_ROW.format(extra="")), _LOCK_PARAMS).scalar())
    except Exception:  # noqa: BLE001
        logger.warning("Metadata: couldn't check whether a reconcile run holds the lock", exc_info=True)
        return None


def _close_abandoned_runs(db: Session, *, holding_lock: bool = False) -> int:
    """Close ``running`` rows whose process stopped mid-run (a restart or deploy) as failed. Commits; returns how many.

    On Postgres every live run holds the advisory lock, so when nobody holds it each ``running``
    row is left over, wherever it ran. ``holding_lock`` says the caller holds it and has no row
    yet (a run starting), so every ``running`` row is. Without that signal, a row counts as left
    over once its process on this host is gone. A very old row does too, unless the lock is held.
    """
    running = (
        db.query(MetadataReconcileRunModel.id, MetadataReconcileRunModel.started_at, MetadataReconcileRunModel.summary)
        .filter(MetadataReconcileRunModel.status == "running")
        .all()
    )
    if not running:
        return 0
    # Rows are read before the lock is checked: a run takes the lock before inserting its row.
    held = False if holding_lock else _run_lock_held(db)
    cutoff = _utcnow() - _ABANDONED_RUN_AGE
    closed = 0
    for run_id, started_at, summary in running:
        left_over = (
            held is False
            or (started_at < cutoff and held is not True)
            or (held is None and _worker_gone((summary or {}).get(_WORKER_KEY)))
        )
        if not left_over:
            continue
        logger.warning("Metadata: reconcile run %s (started %s) stopped without finishing; closing it as failed.",
                       run_id, started_at)
        closed += (
            db.query(MetadataReconcileRunModel)
            .filter(MetadataReconcileRunModel.id == run_id, MetadataReconcileRunModel.status == "running")
            .update({
                MetadataReconcileRunModel.status: "failed",
                MetadataReconcileRunModel.error: _ABANDONED_RUN_ERROR,
                MetadataReconcileRunModel.finished_at: _utcnow(),
                MetadataReconcileRunModel.summary: {k: v for k, v in (summary or {}).items() if k != _WORKER_KEY},
            }, synchronize_session=False)
        )
    if closed:
        db.commit()
    return closed


def start_run(
    db: Session, *, trigger: RunTrigger, mode: ReconcileMode, actor: Optional[str] = None,
    summary: Optional[dict] = None,
) -> str:
    """Insert a ``running`` run so the Runs tab shows it while it works. Commits; returns the run id.

    Runs left ``running`` by a process that stopped mid-run are closed as failed first.
    """
    _close_abandoned_runs(db, holding_lock=_holding_run_lock.get())
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
    """Write the run's statements and counters, close it and commit. Raises ``errors.NotFound``.

    A row another process already closed (it saw the run's lock gone) keeps its status; what
    the run did is still recorded on it.
    """
    run = db.get(MetadataReconcileRunModel, run_id)
    if run is None:
        raise errors.NotFound(f"Reconcile run '{run_id}' not found.")
    db.refresh(run)
    closed_as = run.status if run.status != "running" else None
    if closed_as:
        logger.warning("Metadata reconcile run %s was closed as %s while it was still working; recording what it "
                       "did without reopening it.", run_id, closed_as)
        status = closed_as  # type: ignore[assignment]
        error = _LOST_LOCK_ERROR + (f" {error}" if error else "")
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
    removed = sum(int(o.removals or 0) for o in written if o.status == "applied")
    if removed:
        counts["removals_applied"] = removed
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
    """Runs newest first, without their statements (``get_run`` has those). Closes abandoned rows first."""
    _close_abandoned_runs(db)
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
    """Raises ``errors.NotFound``. Closes abandoned rows first, so a run cut off by a restart reads as failed."""
    _close_abandoned_runs(db)
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

        def still_held() -> bool:
            try:
                held = conn.execute(text(_LOCK_ROW.format(extra=" AND pid = pg_backend_pid()")), _LOCK_PARAMS).scalar()
                conn.commit()
                return bool(held)
            except Exception:  # noqa: BLE001 - a dropped connection means the lock is gone
                logger.warning("Metadata: couldn't confirm the reconcile run still holds its lock", exc_info=True)
                return False

        holding = _holding_run_lock.set(True)
        checking = _lock_check.set(still_held)
        try:
            yield
        finally:
            _lock_check.reset(checking)
            _holding_run_lock.reset(holding)
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
                conn.commit()
            except Exception:  # noqa: BLE001
                logger.warning("Couldn't release the reconcile lock; dropping its connection", exc_info=True)
                # Closing the server session releases the lock instead.
                conn.invalidate()
    finally:
        conn.close()


@contextmanager
def _bounded_waits(db: Session) -> Iterator[None]:
    """Postgres statement and lock timeouts on each of ``db``'s transactions while a run holds the lock.

    A query stuck behind another session's lock would otherwise hang the run, and
    with it every later check, for as long as that session stays open. SET LOCAL ends
    with each transaction, so a pooled connection doesn't keep the limits.
    """
    if db.get_bind().dialect.name != "postgresql":
        yield
        return

    def limit(_session, _transaction, connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {_STATEMENT_TIMEOUT_MS}")
        connection.exec_driver_sql(f"SET LOCAL lock_timeout = {_LOCK_TIMEOUT_MS}")

    event.listen(db, "after_begin", limit)
    try:
        if db.in_transaction():
            limit(db, None, db.connection())
        yield
    finally:
        event.remove(db, "after_begin", limit)


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


# --- Stopping a run that's writing ------------------------------------------------------


def request_shutdown(wait_seconds: float = 10.0) -> None:
    """Tell running reconciles to stop writing, and give detached ones a moment to record it. App shutdown."""
    _shutdown.set()
    deadline = time.monotonic() + max(0.0, wait_seconds)
    for thread in list(_live_runs):
        thread.join(max(0.0, deadline - time.monotonic()))


def _refresh_settings(db: Session) -> None:
    """Pick up Metadata Manager settings saved on another replica."""
    from app.core import settings_store

    settings_store.refresh_overrides(db, "METADATA_")


def _write_scope() -> Tuple:
    return current_mode(), tuple(scope.patterns()), tuple(scope.enforce_patterns())


class _StopCheck:
    """Why a writing run must stop, or None.

    It stops when the app is shutting down, when its connection no longer holds the run
    lock (another run may have started), or when the mode or what it may write changed
    since it started, on this replica or another. Checks at most every few seconds,
    thread-safe; once it says stop it keeps saying so.
    """

    def __init__(self, db: Session, *, interval: Optional[float] = None):
        self._bind = db.get_bind()
        self._lock_alive = _lock_check.get()
        self._scope = _write_scope()
        interval = _STOP_CHECK_SECONDS if interval is None else interval
        self._interval = interval
        self._next = _monotonic() + interval
        self._mutex = threading.Lock()
        self.reason: Optional[str] = None

    def __call__(self, *, force: bool = False) -> Optional[str]:
        with self._mutex:
            if self.reason is None and _shutdown.is_set():
                self.reason = "The app is shutting down."
            if self.reason is not None or (not force and _monotonic() < self._next):
                return self.reason
            self._next = _monotonic() + self._interval
            self.reason = self._check()
            return self.reason

    def _check(self) -> Optional[str]:
        if self._lock_alive is not None and not self._lock_alive():
            return "The run lost its database lock, so another run may have started."
        session = Session(bind=self._bind)
        try:
            _refresh_settings(session)
        finally:
            session.close()
        if _write_scope() != self._scope:
            return "The sync settings changed while it was writing (mode, catalogs or schemas)."
        return None


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
    # A resume's ceiling: the removals the admin reviewed on the paused run.
    max_removals: Optional[int] = None
    # Catalog -> why it was skipped (shown on the run). ``blocked`` ones had nothing planned at all.
    skipped: Dict[str, str] = field(default_factory=dict)
    blocked: Set[str] = field(default_factory=set)
    counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    timings: Dict[str, int] = field(default_factory=dict)
    # Queue keys to retry and why: their catalog couldn't be read, or adopting them failed.
    retry: Dict[str, str] = field(default_factory=dict)
    # Queue keys re-queued for when they've been quiet long enough (not failures).
    waiting: Set[str] = field(default_factory=set)


@dataclass
class _RunReport:
    result: ReconcileResult
    retry: Dict[str, str] = field(default_factory=dict)
    waiting: Set[str] = field(default_factory=set)
    # The run stopped before per-object outcomes were known.
    error: bool = False
    # The stop check ended it mid-apply; its objects aren't failures, just not finished.
    stopped: bool = False


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
    max_removals: Optional[int] = None,
) -> ReconcileResult:
    """Reconcile named objects (``fqn_keys``, filtered to ``catalogs`` if given) or whole catalogs (a sweep).

    A sweep covers ``catalogs``, default the scope (``observed.scope_catalogs``). ``mode``
    defaults to METADATA_RECONCILE_MODE; ``plan`` records statements without running
    any. In ``enforce``, a tripped breaker records a ``paused`` run unless overridden,
    and an overriding run clears it; with ``max_removals`` it applies nothing and stays
    paused if it would remove more than that. Unmanaged keys named here are adopted under
    METADATA_NEW_OBJECT_POLICY=adopt. Raises ``ReconcileBusy``; any other failure is
    recorded as a ``failed`` run and returned.
    """
    return _run(
        db, trigger=trigger, fqn_keys=fqn_keys, catalogs=catalogs, mode=mode, actor=actor,
        provider=provider, override=override_circuit_breaker, max_removals=max_removals,
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
    max_removals: Optional[int] = None,
    wait_seconds: float = _ACCEPT_SECONDS,
    session_factory: Optional[Callable[[], Session]] = None,
) -> ReconcileResult:
    """Start ``run`` on its own session and thread.

    If it finishes within ``wait_seconds``, return that result (empty catalogs, a
    lock conflict, a skipped mode). Otherwise return the still-running row so an
    HTTP caller can hang up; the thread keeps sweeping. A warehouse-sized catalog
    takes minutes and would otherwise time out the request and leave the UI stuck
    on Checking.
    """
    from app.db.session import get_lakebase_session

    factory = session_factory or get_lakebase_session
    box: Dict[str, object] = {}

    def work() -> None:
        db = factory()
        try:
            box["result"] = run(
                db, trigger=trigger, fqn_keys=fqn_keys, catalogs=catalogs, mode=mode, actor=actor,
                override_circuit_breaker=override_circuit_breaker, max_removals=max_removals,
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


class PauseChanged(errors.MetadataError):
    """The paused run the admin reviewed isn't the one the breaker holds now (409)."""

    status_code = 409


def _reviewed_pause(db: Session, run_id: str) -> dict:
    breaker = circuit_breaker_state(db)
    if breaker is None:
        raise PauseChanged("Syncing isn't paused any more. Reload Sync history.")
    if breaker.get("run_id") != run_id:
        raise PauseChanged("Syncing paused again on a different sync since you opened it. Review that one instead.")
    return breaker


def resume_paused(db: Session, *, run_id: str, actor: str, **detach) -> ReconcileResult:
    """Apply the paused run ``run_id`` after review: re-check only its objects, and apply only if that
    removes no more than it planned. Otherwise the new run is paused for review instead.

    Raises ``PauseChanged`` when the breaker no longer holds ``run_id``.
    """
    _reviewed_pause(db, run_id)
    if current_mode() != "enforce":
        raise errors.MetadataError("Syncing is report only now, so there's nothing to apply. Clear the pause instead.")
    rows = (
        db.query(MetadataReconcileItemModel.fqn_key, MetadataReconcileItemModel.removals)
        .filter(MetadataReconcileItemModel.run_id == run_id, MetadataReconcileItemModel.status == "planned")
        .all()
    )
    keys = sorted({queue_key(k) for k, _ in rows})
    if not keys:
        raise errors.MetadataError("The paused sync has nothing planned to apply. Clear the pause instead.")
    reviewed = sum(int(r or 0) for _, r in rows)
    logger.info("Metadata: %s resumed paused run %s (%d object(s), %d removal(s) reviewed).",
                actor, run_id, len(keys), reviewed)
    return run_detached(
        trigger="manual", fqn_keys=keys, actor=actor, override_circuit_breaker=True, max_removals=reviewed, **detach,
    )


def clear_paused(db: Session, *, run_id: str, actor: str) -> None:
    """Lift the pause on ``run_id`` without applying its removals. Commits.

    Raises ``PauseChanged`` when the breaker no longer holds ``run_id``.
    """
    _reviewed_pause(db, run_id)
    clear_circuit_breaker(db, cleared_by=actor)
    db.commit()
    logger.warning("Metadata: %s cleared the pause on run %s without applying its removals.", actor, run_id)


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
    max_removals: Optional[int] = None,
) -> _RunReport:
    """``run``, plus what ``drain_queue`` needs; ``adopt`` limits adoption to those keys (None: all named)."""
    _refresh_settings(db)
    mode = mode or current_mode()
    if mode == "enforce" and not scope.is_explicit():
        mode = "plan"
    if mode == "off":
        return _RunReport(record_run(
            db, trigger=trigger, mode="off", status="skipped", actor=actor,
            paused_reason="Reconciliation is off (METADATA_RECONCILE_MODE).",
        ))
    with _single_flight(db), _bounded_waits(db):
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
        run_summary: dict = {"scope": run_scope, **({"override": True} if override and mode == "enforce" else {})}
        run_id = start_run(db, trigger=trigger, mode=mode, actor=actor, summary=run_summary)
        # Every line the run logs, down to each warehouse read, carries req_id "metadata_reconcile:<run id>".
        with job_log_context("metadata_reconcile", run_id):
            logger.info(
                "Metadata reconcile %s run %s (%s) started by %s: %s.", trigger, run_id, mode, actor or "the app", run_scope,
            )
            ctx = _Context(
                run_id=run_id, trigger=trigger, mode=mode, actor=actor, provider=provider, override=override,
                now=_utcnow(), exempt=policy.exempt_keys(), max_removals=max_removals if override else None,
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
    stop = _StopCheck(db) if ctx.mode == "enforce" else None
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
    recent = recent_removals(db, now=ctx.now) if ctx.mode == "enforce" and not ctx.override and removals else 0
    paused_reason: Optional[str] = None
    executed = False
    if ctx.mode == "enforce" and not ctx.override and removals + recent > limit:
        breaker = trip_circuit_breaker(db, run_id=ctx.run_id, removals=removals, limit=limit, recent=recent)
        paused_reason = _paused_reason(breaker)
        outcomes = [_planned(i) for i in items]
    elif ctx.mode == "enforce" and ctx.max_removals is not None and removals > ctx.max_removals:
        breaker = trip_circuit_breaker(
            db, run_id=ctx.run_id, removals=removals, limit=limit,
            reason=(f"Resuming would now remove {removals} tags or descriptions, more than the {ctx.max_removals} "
                    "reviewed on the paused sync, so nothing was applied. Review this sync's removals and resume again."),
        )
        paused_reason = _paused_reason(breaker)
        outcomes = [_planned(i) for i in items]
    elif ctx.mode == "enforce" and items and stop is not None and stop(force=True):
        outcomes = [apply_.not_run(i, stop.reason) for i in items]
    elif ctx.mode == "enforce" and items:
        applying = _monotonic()
        outcomes = apply_.execute(ctx.provider, items, workers=_apply_workers(), stop=stop)
        ctx.timings["apply_ms"] = _ms(applying)
        executed = True
        _patch_snapshot(db, outcomes)
    else:
        outcomes = [_planned(i) for i in items]
    stopped = stop.reason if stop is not None and paused_reason is None and items else None
    if stopped:
        logger.warning("Metadata reconcile run %s stopped writing: %s", ctx.run_id, stopped)
    if ctx.mode == "enforce" and ctx.override and paused_reason is None and not stopped:
        if clear_circuit_breaker(db, cleared_by=ctx.actor or "override"):
            logger.info("Metadata reconcile run %s cleared the circuit breaker (override).", ctx.run_id)
    outcomes = list(outcomes) + reported

    _record_objects(db, works, outcomes, executed=executed, now=ctx.now, reported=reported_keys)
    ctx.timings["total_ms"] = _ms(started)
    if paused_reason:
        status: RunStatus = "paused"
    elif stopped:
        status = "partial" if any(o.status in ("applied", "noop") for o in outcomes) else "failed"
    else:
        status = run_status(outcomes, mode=ctx.mode)
    result = finish_run(
        db, ctx.run_id,
        status=status,
        outcomes=outcomes,
        objects_checked=sum(1 for w in works if w.present),
        objects_drifted=sum(1 for w in works if w.items),
        skipped_catalogs=list(ctx.skipped),
        paused_reason=paused_reason,
        error=f"Stopped before writing everything: {stopped} The rest is picked up by the next run." if stopped else None,
        summary={
            "counts": dict(ctx.counts), "skipped": dict(ctx.skipped), "unread": sorted(ctx.blocked),
            "timings_ms": dict(ctx.timings),
        },
    )
    _log_run(result, ctx)
    if paused_reason:
        _report_breaker_trip(ctx, removals, limit, recent)
    if ctx.trigger == "sweep" and ctx.mode == "enforce" and not stopped:
        _purge_orphans(db)
    if ctx.trigger == "sweep" and not stopped:
        _prune_history(db)
    return _RunReport(result, retry=dict(ctx.retry), waiting=set(ctx.waiting), stopped=bool(stopped))


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

    # A refresh returns the catalog's own row whenever it could read the catalog.
    unreadable = sorted({k.catalog.lower() for k in requested if k.fqn_key not in seen and k.catalog.lower() not in seen})
    for catalog in unreadable:
        ctx.skipped[catalog] = "Couldn't read it from Unity Catalog; its objects will be retried."
        ctx.blocked.add(catalog)
    withheld = {k for k in keys if k not in seen}
    for key in sorted(withheld):
        ctx.retry[key] = ctx.skipped.get(_catalog_of(key)) or (
            "Its schema didn't come back from Unity Catalog although it was there before; it will be retried, "
            "and the next full check confirms whether the schema was dropped."
        )
    keys = [k for k in keys if k not in withheld]

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
    vanished = [] if wanted else snapshot.vanished_catalogs(db, targets)
    if not targets and not vanished:
        return []
    swept = snapshot.sweep(db, provider=ctx.provider, catalogs=targets, vanished=vanished)
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
    waiting = _still_changing(candidates, seen)
    _wait_until_quiet(db, ctx, waiting)
    candidates = [k for k in candidates if k not in waiting]
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


def _still_changing(keys: Iterable[str], seen: Dict[str, ObservedState]) -> Dict[str, datetime]:
    """Of these keys, those UC changed within METADATA_NEW_OBJECT_QUIET_MINUTES, with when that ends.

    A column goes by its relation, whose times change when the column does.
    """
    minutes = _int_setting("METADATA_NEW_OBJECT_QUIET_MINUTES", 10)
    if minutes <= 0:
        return {}
    now = _utcnow()
    waiting: Dict[str, datetime] = {}
    for key in keys:
        obs = seen.get(_relation_of(key) if COLUMN_SEPARATOR in key else key)
        stamps = [t for t in (obs.created, obs.last_altered) if t is not None] if obs is not None else []
        quiet_at = max(stamps) + timedelta(minutes=minutes) if stamps else None
        if quiet_at is not None and quiet_at > now:
            waiting[key] = quiet_at
    return waiting


def _wait_until_quiet(db: Session, ctx: _Context, waiting: Dict[str, datetime]) -> None:
    """Queue objects that changed too recently to take over for when they've gone quiet."""
    if not waiting:
        return
    queued = enqueue(db, list(waiting), reason="new_object", not_before=max(waiting.values()))
    fresh = set(queued) - ctx.waiting
    ctx.waiting.update(queued)
    ctx.counts["waiting"] += len(fresh)
    logger.info("Metadata reconcile: %d new or recreated object(s) changed in the last %s minute(s); "
                "left alone until they've been quiet that long.", len(fresh),
                _int_setting("METADATA_NEW_OBJECT_QUIET_MINUTES", 10))


def _created_anew(row: DesiredState, obs: ObservedState, relation_new: bool) -> bool:
    """Whether UC's object is new to this desired row: it was pending or orphaned, UC reports a
    later created time than the one last reconciled against, or (a column) its relation is new."""
    if row.object_state in ("pending", "orphaned") or relation_new:
        return True
    return (row.observed_created is not None and obs.created is not None
            and obs.created - row.observed_created > _CREATED_SLACK)


def _merge_created(db: Session, ctx: _Context, keys: Sequence[str]) -> Set[str]:
    """Add the tags new or recreated objects were created with to their desired state; returns the keys
    that couldn't be merged, which mustn't be planned (syncing would remove those tags)."""
    from app.services.metadata import changes

    db.commit()
    try:
        changes.merge_observed(db, keys, reason=_MERGE_REASON)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning("Metadata reconcile: couldn't keep the create-time tags of %d object(s): %s",
                       len(keys), e, exc_info=True)
        for key in keys:
            ctx.retry[queue_key(key)] = _clip(f"Couldn't keep the tags it was created with: {e}")
        return set(keys)
    ctx.counts["created_anew"] += len(keys)
    return set()


def _seen_type(desired: DesiredState, observed: ObservedState) -> Optional[str]:
    """UC's relation type when it differs from the desired row's (a view created as pending 'table')."""
    if observed.securable_type in RELATION_TYPES and observed.securable_type != desired.securable_type:
        return observed.securable_type
    return None


def _plan_objects(
    db: Session, ctx: _Context, desired: Dict[str, DesiredState], seen: Dict[str, ObservedState],
) -> List[_Work]:
    """Lifecycle (pending/orphaned -> exists, exists -> orphaned), then each present row's statements.

    An object new to its row (``_created_anew``) is left alone while it's still changing,
    then keeps the tags it was created with before it's planned, so syncing adds the
    desired state instead of removing what its creator set.
    """
    from app.services.metadata import store

    keys = [k for k in sorted(desired) if _catalog_of(k) not in ctx.blocked]
    anew: Dict[str, None] = {}
    for fqn_key in keys:
        obs = seen.get(fqn_key)
        relation_new = COLUMN_SEPARATOR in fqn_key and _relation_of(fqn_key) in anew
        if obs is not None and obs.present and _created_anew(desired[fqn_key], obs, relation_new):
            anew[fqn_key] = None
    waiting = _still_changing(anew, seen)
    _wait_until_quiet(db, ctx, waiting)
    merging = [k for k in anew if k not in waiting]
    held = set(waiting) | (_merge_created(db, ctx, merging) if merging else set())
    if merging and not held & set(merging):
        desired = {**desired, **store.get_objects(db, merging)}

    works: List[_Work] = []
    stamps: List[dict] = []
    for fqn_key in keys:
        if fqn_key in held:
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
        if present and obs.created is not None and (
            row.observed_created is None or abs(obs.created - row.observed_created) > _CREATED_SLACK
        ):
            stamps.append({"b_id": row.id, "b_created": obs.created})
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
    if stamps:
        db.execute(
            update(_OBJECTS).where(_OBJECTS.c.id == bindparam("b_id")).values(
                observed_created=bindparam("b_created", type_=_OBJECTS.c.observed_created.type),
            ),
            stamps,
        )
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
        "%d created or recreated, %d waiting to go quiet, %d orphaned; %d catalog(s) skipped. "
        "Observe %s ms, apply %s ms, total %s ms.",
        result.trigger, result.run_id, result.mode, result.status, result.objects_checked, result.objects_drifted,
        result.statements_planned, result.statements_applied, result.statements_failed, result.removals_planned,
        result.statements_reported,
        counts.get("adopted", 0), counts.get("now_exist", 0), counts.get("created_anew", 0), counts.get("waiting", 0),
        counts.get("orphaned", 0), len(result.skipped_catalogs),
        ctx.timings.get("observe_ms", 0), ctx.timings.get("apply_ms", 0), ctx.timings.get("total_ms", 0),
    )


def _report_breaker_trip(ctx: _Context, removals: int, limit: int, recent: int = 0) -> None:
    logger.warning(
        "Metadata reconcile run %s tripped the circuit breaker: it would remove %d tags or descriptions, with %d "
        "already removed in the last hour (limit %d an hour). Nothing was applied; enforcement is paused until an "
        "admin resumes or clears it.", ctx.run_id, removals, recent, limit,
    )
    _alert_governance(
        "[Action required] Metadata reconciler paused: too many removals",
        [
            f"A {ctx.trigger} run would remove {removals} tags or descriptions from Unity Catalog"
            + (f", after {recent} already removed in the last hour" if recent else "")
            + f"; the limit is {limit} an hour (METADATA_MAX_REMOVALS_PER_RUN).",
            "Nothing was applied. Runs that change Unity Catalog stay paused until an admin reviews the paused "
            "run under Sync history and either applies its removals or clears the pause without applying them.",
            f"Run: {ctx.run_id}",
        ],
    )


def _purge_orphans(db: Session) -> None:
    from app.services.metadata import store

    days = _int_setting("METADATA_ORPHAN_RETENTION_DAYS", 30)
    if days < 1:
        return
    try:
        purged = store.purge_orphans(db, older_than_days=days, read_after=snapshot.confirmed_reads(db))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning("Metadata reconcile: purging orphaned objects failed: %s", e, exc_info=True)
        return
    if purged:
        logger.info("Metadata reconcile purged %d object(s) orphaned for more than %d days.", purged, days)


def _prune_history(db: Session, *, budget_seconds: float = 60.0) -> int:
    """Delete runs older than METADATA_RUN_HISTORY_DAYS with their statements; returns the runs deleted.

    Paused and running runs and the newest run of each trigger are kept. Works in
    small committed batches and stops after ``budget_seconds``; the next sweep goes on.
    """
    days = _int_setting("METADATA_RUN_HISTORY_DAYS", 30)
    if days < 1:
        return 0
    deadline = _monotonic() + budget_seconds
    pruned = 0
    try:
        newest = {
            db.execute(
                select(_RUNS.c.id).where(_RUNS.c.trigger == trigger).order_by(_RUNS.c.started_at.desc()).limit(1)
            ).scalar()
            for trigger in db.execute(select(_RUNS.c.trigger).distinct()).scalars()
        }
        old = (
            select(_RUNS.c.id)
            .where(_RUNS.c.started_at < _utcnow() - timedelta(days=days), _RUNS.c.status.not_in(("paused", "running")),
                   _RUNS.c.id.not_in(newest - {None}))
            .order_by(_RUNS.c.started_at)
            .limit(_PRUNE_BATCH)
        )
        while _monotonic() < deadline:
            ids = list(db.execute(old).scalars())
            if not ids:
                break
            items_gone = False
            while not items_gone and _monotonic() < deadline:
                batch = select(_ITEMS.c.id).where(_ITEMS.c.run_id.in_(ids)).limit(_WRITE_CHUNK * 10)
                items_gone = not db.execute(delete(_ITEMS).where(_ITEMS.c.id.in_(batch))).rowcount
                db.commit()
            if not items_gone:
                break
            db.execute(delete(_RUNS).where(_RUNS.c.id.in_(ids)))
            db.commit()
            pruned += len(ids)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning("Metadata reconcile: pruning sync history failed: %s", e, exc_info=True)
    if pruned:
        logger.info("Metadata reconcile deleted %d run(s) older than %d days from Sync history.", pruned, days)
    return pruned


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
    if result.status in ("paused", "skipped") or report.stopped:
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
    written = {queue_key(o.fqn_key) for o in result.items if o.status == "applied"}
    seen_in_uc = [r.fqn_key for r in rows if r.reason in _OBSERVED_REASONS and r.fqn_key in written]
    for key, runs in _rewrites(db, seen_in_uc, since=now - _REWRITE_WINDOW).items():
        if runs >= _REWRITE_LIMIT and key not in failures:
            failures[key] = (
                f"Written {runs} times in the last hour: something else keeps changing it back, or Unity Catalog "
                "stores a value differently from how it was written. Retried less often until it stays in sync."
            )
    deletes: List[dict] = []
    backoffs: List[dict] = []
    exhausted: List[Tuple[str, str]] = []
    for row in rows:
        if row.fqn_key in report.waiting:
            continue
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


def _rewrites(db: Session, keys: Sequence[str], *, since: datetime) -> Dict[str, int]:
    """Queue key -> how many runs started since ``since`` applied a statement to it or its columns."""
    runs: Dict[str, Set[str]] = defaultdict(set)
    for start in range(0, len(keys), 50):
        chunk = keys[start:start + 50]
        matches = [_ITEMS.c.fqn_key.in_(chunk)] + [
            _ITEMS.c.fqn_key.startswith(f"{key}{COLUMN_SEPARATOR}", autoescape=True) for key in chunk
        ]
        for fqn_key, run_id in db.execute(
            select(_ITEMS.c.fqn_key, _ITEMS.c.run_id).join(_RUNS, _RUNS.c.id == _ITEMS.c.run_id)
            .where(_RUNS.c.started_at >= since, _ITEMS.c.status == "applied", or_(*matches))
        ):
            runs[queue_key(fqn_key)].add(run_id)
    return {key: len(ids) for key, ids in runs.items() if key in keys}


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
    if _shutdown.is_set():
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
    """One poller cycle: the new-object poll when due, drain batches until the budget runs out, then a due sweep.

    Settings saved on another replica are picked up first, so turning syncing off there stops it here.
    """
    global _pass_error_noted
    deadline = _monotonic() + budget_seconds
    db = _session()
    try:
        _refresh_settings(db)
        db.commit()
        if current_mode() == "off":
            return
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
        if sweep and not _shutdown.is_set():
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
    while _monotonic() < deadline and not _shutdown.is_set():
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
