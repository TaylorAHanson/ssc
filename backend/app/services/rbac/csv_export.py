"""The scheduled full-snapshot CSV export, one file per sheet (ADR 0005 D2).

Files reproduce a manual Excel "Save as CSV" of each sheet: recorded headers in
recorded order, rows in position order, minimal quoting, CRLF after every row,
UTF-8 without a byte-order mark (the last two are settings). Export folders hold
exactly those CSVs, like today's manual export; the run's manifest (per-file
path, sha256 and row count, plus the change summary and watermark) lives on the
``rbac_exports`` row.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
import os
import re
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from croniter import CroniterBadCronError, croniter
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import _yaml_config, settings
from app.db.rbac import (
    RULE_ACTIVE,
    SOURCE_REQUEST,
    RbacChangeSetModel,
    RbacExportFileModel,
    RbacExportModel,
    RbacRuleEventModel,
    RbacRuleModel,
    RbacSheetModel,
)
from app.models.rbac import (
    CANONICAL_FIELDS,
    CompareDifference,
    CompareResult,
    ExportFile,
    ExportRun,
    ExportStatus,
    ExportTarget,
    PathCheckResult,
)
from app.services.rbac import audit, layout

logger = logging.getLogger(__name__)

# Cached next scheduled run; settings_store clears it when RBAC_EXPORT_CRON changes.
_next_export_time: Optional[datetime] = None
# When the first change not yet exported was noted (RBAC_EXPORT_ON_CHANGE).
_change_pending_since: Optional[datetime] = None
# The invalid RBAC_EXPORT_CRON already logged, so a bad value is reported once.
_bad_cron: Optional[str] = None

ON_CHANGE_DELAY_SECONDS = 60
TRIGGERS = ("schedule", "manual", "on_change")
EXPORTED_FACT = "rbac_exported"
BOM = b"\xef\xbb\xbf"
NO_PATH = "No export path set"
LOCAL_PATH_REFUSED = "Local folders are only allowed in local development; use a /Volumes/ or /Workspace/ path."
COMPARE_ROWS_CAP = 50

# Session-level Postgres advisory lock key that keeps exports single flight across replicas.
_ADVISORY_LOCK_KEY = 5_005_202_610_160_001
_local_lock = threading.Lock()

_CANONICAL = frozenset(CANONICAL_FIELDS)
_CSV_TOKEN = re.compile(r'"[^"]*"|\r\n|\r|\n')


class ExportBusy(Exception):
    """Another export is running."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Rendering ----------------------------------------------------------------


def _field(value: str) -> str:
    if "," in value or '"' in value or "\r" in value or "\n" in value:
        return '"' + value.replace('"', '""') + '"'
    return value


def _encode(rows: Sequence[Sequence[str]]) -> bytes:
    eol = "\n" if (settings.RBAC_CSV_LINE_ENDING or "").strip().lower() == "lf" else "\r\n"
    data = "".join(",".join(_field(cell) for cell in row) + eol for row in rows).encode("utf-8")
    return BOM + data if settings.RBAC_CSV_BOM else data


def _layout(db: Session, sheet: str) -> Tuple[str, List[Dict[str, Any]]]:
    """The sheet's canonical name and column layout (recorded at import, else the plane's default)."""
    spec = layout.sheet_spec(sheet)
    name = spec.name if spec else sheet
    recorded = db.get(RbacSheetModel, name)
    if recorded is not None and recorded.columns:
        return name, list(recorded.columns)
    plane = spec.plane if spec else (recorded.plane if recorded is not None else None)
    if plane is None:
        raise ValueError(f'"{sheet}" isn\'t a managed sheet.')
    return name, layout.default_columns(plane)


def _cells(rule: RbacRuleModel, columns: Sequence[Dict[str, Any]]) -> List[str]:
    extra = rule.extra or {}
    out: List[str] = []
    for idx, col in enumerate(columns):
        fld = col.get("field")
        if fld in _CANONICAL:
            out.append(getattr(rule, fld) or "")
        else:
            value = extra.get(str(idx))
            out.append("" if value is None else str(value))
    return out


def _render(db: Session, sheet: str) -> Tuple[bytes, int]:
    name, columns = _layout(db, sheet)
    rules = (
        db.query(RbacRuleModel)
        .filter(RbacRuleModel.sheet == name, RbacRuleModel.status == RULE_ACTIVE)
        .order_by(RbacRuleModel.position, RbacRuleModel.created_at, RbacRuleModel.id)
        .all()
    )
    rows: List[List[str]] = [[str(c.get("header") or "") for c in columns]]
    rows.extend(_cells(rule, columns) for rule in rules)
    return _encode(rows), len(rules)


def render_sheet(db: Session, sheet: str) -> bytes:
    """The CSV bytes for one sheet's active rules."""
    return _render(db, sheet)[0]


# --- Destinations -------------------------------------------------------------


def _path_kind(path: str) -> str:
    if path.startswith("/Volumes/"):
        return "volume"
    if path.startswith("/Workspace/"):
        return "workspace"
    return "local"


def _local_allowed() -> bool:
    from app.core.config import dev_features_allowed

    return dev_features_allowed()


class _Storage:
    """Writes to a UC Volume or a Workspace folder as the app's service principal, or to a local folder."""

    def __init__(self) -> None:
        self._client: Any = None
        self._client_error: Optional[Exception] = None

    @property
    def client(self) -> Any:
        if self._client_error is not None:
            raise self._client_error
        if self._client is None:
            try:
                from databricks.sdk import WorkspaceClient

                self._client = WorkspaceClient()
            except Exception as exc:  # noqa: BLE001 - reported per file
                self._client_error = exc
                raise
        return self._client

    def ensure_dir(self, kind: str, folder: str) -> None:
        """Best effort: a failure here shows up as the write's error."""
        try:
            if kind == "volume":
                self.client.files.create_directory(folder)
            elif kind == "workspace":
                self.client.workspace.mkdirs(folder)
            else:
                Path(folder).mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Couldn't create export folder %s: %s", folder, exc)

    def write(self, kind: str, file_path: str, data: bytes) -> None:
        if kind == "volume":
            self.client.files.upload(file_path, io.BytesIO(data), overwrite=True)
        elif kind == "workspace":
            from databricks.sdk.service.workspace import ImportFormat

            self.client.workspace.upload(file_path, io.BytesIO(data), format=ImportFormat.AUTO, overwrite=True)
        else:
            target = Path(file_path)
            tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, target)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise

    def delete(self, kind: str, file_path: str) -> None:
        if kind == "volume":
            self.client.files.delete(file_path)
        elif kind == "workspace":
            self.client.workspace.delete(file_path)
        else:
            Path(file_path).unlink()


def _error_names(exc: BaseException) -> Set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def _describe_error(exc: BaseException, kind: str) -> str:
    detail = (str(exc).strip() or exc.__class__.__name__)[:500]
    names = _error_names(exc)
    if "PermissionError" in names or "PermissionDenied" in names or "PERMISSION_DENIED" in detail:
        if kind == "local":
            return f"Permission denied: the app can't write to this folder. ({detail})"
        return f"Permission denied: the app's service principal needs write access to this folder. ({detail})"
    if "NotFound" in names or "RESOURCE_DOES_NOT_EXIST" in detail:
        return f"The folder doesn't exist, or the app's service principal can't see it. ({detail})"
    return detail


def _target_label(key: str, label: str) -> str:
    return "the metastore sheet" if key == layout.METASTORE_TARGET else f'the "{label}" environment'


def _join(names: Sequence[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


# --- Export run ---------------------------------------------------------------


@contextmanager
def _single_flight(db: Session) -> Iterator[None]:
    """One export at a time: a Postgres advisory lock across replicas, an in-process lock on SQLite."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        if not _local_lock.acquire(blocking=False):
            raise ExportBusy("An export is already running.")
        try:
            yield
        finally:
            _local_lock.release()
        return

    # A dedicated connection holds the session-level lock for the whole run; the
    # ORM session may hand its own connection back to the pool on each commit.
    engine = getattr(bind, "engine", bind)
    conn = engine.connect()
    try:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}).scalar()
        conn.commit()
        if not got:
            raise ExportBusy("An export is already running.")
        try:
            yield
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
                conn.commit()
            except Exception:  # noqa: BLE001
                logger.warning("Couldn't release the export lock; dropping its connection", exc_info=True)
                # Closing the server session releases the lock instead.
                conn.invalidate()
    finally:
        conn.close()


def _last_complete_watermark(db: Session) -> Optional[int]:
    value = (
        db.query(func.max(RbacExportModel.watermark))
        .filter(RbacExportModel.status == "completed")
        .scalar()
    )
    return None if value is None else int(value)


def _file_row(export_id: str, sheet: str, path: Optional[str], status: str, created_at: datetime,
              error: Optional[str] = None) -> RbacExportFileModel:
    return RbacExportFileModel(
        id=audit.new_id(), export_id=export_id, sheet=sheet, path=path, status=status,
        error=error, row_count=0, size_bytes=0, created_at=created_at,
    )


def _write_targets(db: Session, export_id: str, now: datetime) -> Tuple[List[RbacExportFileModel], List[str]]:
    files: List[RbacExportFileModel] = []
    warnings: List[str] = []
    storage = _Storage()
    for key, label, path, sheets in layout.export_targets():
        if not sheets:
            continue
        if not path:
            files.extend(_file_row(export_id, s, None, "skipped", now, NO_PATH) for s in sheets)
            verb = "was" if len(sheets) == 1 else "were"
            warnings.append(f"No export path is set for {_target_label(key, label)}, "
                            f"so {_join(sheets)} {verb} not exported.")
            continue
        kind = _path_kind(path)
        folder = path.rstrip("/")
        if kind == "local" and not _local_allowed():
            files.extend(_file_row(export_id, s, f"{folder}/{s}.csv", "failed", now, LOCAL_PATH_REFUSED)
                         for s in sheets)
            continue
        storage.ensure_dir(kind, folder)
        for sheet in sheets:
            row = _file_row(export_id, sheet, f"{folder}/{sheet}.csv", "failed", now)
            try:
                data, count = _render(db, sheet)
                row.sha256 = hashlib.sha256(data).hexdigest()
                row.row_count = count
                row.size_bytes = len(data)
                storage.write(kind, row.path, data)
            except Exception as exc:  # noqa: BLE001 - one bad file mustn't stop the others
                logger.warning("Couldn't export %s to %s: %s", sheet, row.path, exc)
                row.error = _describe_error(exc, kind)
            else:
                row.status = "written"
                row.content = data.decode("utf-8")
            files.append(row)
    return files, warnings


def _change_summary(db: Session, since: int, upto: int) -> Tuple[Dict[str, Any], Dict[str, Optional[Set[str]]]]:
    """Counts per action and the change sets / requests with events in ``(since, upto]``.

    Also returns, per request id, the sheets its change sets touched (None if unknown).
    """
    E = RbacRuleEventModel
    grouped = (
        db.query(E.change_set_id, E.action, func.count(E.id), func.min(E.id))
        .filter(E.id > since, E.id <= upto)
        .group_by(E.change_set_id, E.action)
        .all()
    )
    counts: Counter = Counter()
    first_event: Dict[str, int] = {}
    for cs_id, action, n, first in grouped:
        counts[action] += int(n)
        first_event[cs_id] = min(first_event.get(cs_id, first), first)
    cs_ids = sorted(first_event, key=first_event.get)

    change_sets: Dict[str, RbacChangeSetModel] = {}
    for i in range(0, len(cs_ids), 500):
        for cs in db.query(RbacChangeSetModel).filter(RbacChangeSetModel.id.in_(cs_ids[i:i + 500])).all():
            change_sets[cs.id] = cs

    request_sheets: Dict[str, Optional[Set[str]]] = {}
    for cs_id in cs_ids:
        cs = change_sets.get(cs_id)
        if cs is None or cs.source != SOURCE_REQUEST or not cs.source_ref:
            continue
        sheets = set((cs.summary or {}).get("sheets") or [])
        if cs.source_ref not in request_sheets:
            request_sheets[cs.source_ref] = sheets or None
        elif request_sheets[cs.source_ref] is not None:
            request_sheets[cs.source_ref] = (request_sheets[cs.source_ref] | sheets) if sheets else None

    summary = {
        "since_watermark": since,
        "created": counts.get("create", 0),
        "updated": counts.get("update", 0),
        "deleted": counts.get("delete", 0),
        "restored": counts.get("restore", 0),
        "change_sets": cs_ids,
        "request_ids": list(request_sheets),
    }
    return summary, request_sheets


def _add_exported_facts(db: Session, run: RbacExportModel, request_sheets: Dict[str, Optional[Set[str]]],
                        written: Sequence[str]) -> int:
    """An ``rbac_exported`` fact per request, naming its sheets written for the first time."""
    if not request_sheets or not written:
        return 0
    from app.db import EventModel, RequestModel
    from app.state_machines.facts import add_fact

    ids = list(request_sheets)
    existing = {rid for (rid,) in db.query(RequestModel.id).filter(RequestModel.id.in_(ids)).all()}
    reported: Dict[str, Set[str]] = {}
    prior = (
        db.query(EventModel.request_id, EventModel.event_data)
        .filter(EventModel.request_id.in_(ids), EventModel.event_type == EXPORTED_FACT)
        .all()
    )
    for rid, data in prior:
        reported.setdefault(rid, set()).update((data or {}).get("files") or [])

    added = 0
    for rid in ids:
        if rid not in existing:
            continue
        touched = request_sheets[rid]
        files = [s for s in written if (touched is None or s in touched) and s not in reported.get(rid, set())]
        if not files:
            continue
        add_fact(db, rid, EXPORTED_FACT, {"export_id": run.id, "files": files, "watermark": run.watermark},
                 actor="system", commit=False)
        added += 1
    return added


def _prune(db: Session) -> None:
    """Drop archived file content older than RBAC_EXPORT_ARCHIVE_DAYS (the run and checksums stay)."""
    try:
        days = int(settings.RBAC_EXPORT_ARCHIVE_DAYS or 0)
    except (TypeError, ValueError):
        return
    if days <= 0:
        return
    cutoff = _utcnow() - timedelta(days=days)
    pruned = (
        db.query(RbacExportFileModel)
        .filter(RbacExportFileModel.created_at < cutoff, RbacExportFileModel.content.isnot(None))
        .update({RbacExportFileModel.content: None}, synchronize_session=False)
    )
    if pruned:
        logger.info("Pruned the archived content of %d export files older than %d days", pruned, days)


def _file_dto(row: RbacExportFileModel) -> ExportFile:
    return ExportFile(
        id=row.id, sheet=row.sheet, path=row.path, sha256=row.sha256, row_count=row.row_count or 0,
        size_bytes=row.size_bytes or 0, status=row.status, error=row.error,
        downloadable=row.status == "written" and row.content is not None,
    )


def _run_dto(run: RbacExportModel, files: Sequence[ExportFile]) -> ExportRun:
    return ExportRun(
        id=run.id, trigger=run.trigger, actor=run.actor, status=run.status, watermark=run.watermark or 0,
        change_summary=dict(run.change_summary or {}), warnings=list(run.warnings or []), error=run.error,
        started_at=run.started_at, finished_at=run.finished_at, files=list(files),
    )


def _skip(db: Session, trigger: str, actor: Optional[str], watermark: int, reason: str,
          now: datetime) -> ExportRun:
    """A skipped run: recorded for a manual export, returned unsaved for the schedule."""
    if trigger != "manual":
        return ExportRun(id="", trigger=trigger, actor=actor, status="skipped", watermark=watermark,
                         warnings=[reason], started_at=now, finished_at=now)
    run = RbacExportModel(id=audit.new_id(), trigger=trigger, actor=actor, status="skipped", watermark=watermark,
                          change_summary={}, warnings=[reason], started_at=now, finished_at=now)
    db.add(run)
    db.commit()
    return _run_dto(run, [])


def _export(db: Session, *, trigger: str, actor: Optional[str], force: bool) -> ExportRun:
    started = _utcnow()
    current = audit.committed_watermark(db)
    baseline = _last_complete_watermark(db)
    if not force:
        if current == 0:
            return _skip(db, trigger, actor, current, "Nothing to export yet: the ledger is empty. "
                         "Import the workbook first.", started)
        if baseline is not None and baseline == current:
            return _skip(db, trigger, actor, current, "No changes since the last export.", started)
    if trigger != "manual" and not any(path for _, _, path, _ in layout.export_targets()):
        return _skip(db, trigger, actor, current, "No export path is set for any sheet.", started)

    export_id = audit.new_id()
    files, warnings = _write_targets(db, export_id, started)
    written = [f for f in files if f.status == "written"]
    failed = [f for f in files if f.status == "failed"]
    error = None
    if failed:
        status = "partial" if written else "failed"
        if not written:
            error = "No file could be written. Each file shows why."
    elif written:
        status = "completed"
    else:
        status = "skipped"
        warnings.append("Nothing was written: no export path is set for any sheet.")

    summary, request_sheets = _change_summary(db, baseline or 0, current)
    summary["manifest"] = {
        "export_id": export_id,
        "generated_at": started.isoformat() + "Z",
        "watermark": current,
        "files": [
            {"sheet": f.sheet, "path": f.path, "sha256": f.sha256, "rows": f.row_count,
             "size_bytes": f.size_bytes, "status": f.status}
            for f in files
        ],
    }
    run = RbacExportModel(
        id=export_id, trigger=trigger, actor=actor, status=status, watermark=current,
        change_summary=summary, warnings=warnings, error=error, started_at=started, finished_at=_utcnow(),
    )
    db.add(run)
    db.add_all(files)
    facts = 0
    if status in ("completed", "partial"):
        facts = _add_exported_facts(db, run, request_sheets, [f.sheet for f in written])
    _prune(db)
    db.commit()
    logger.info("Access rules export %s (%s): %s, %d written, %d failed, watermark %d, %d request facts",
                export_id, trigger, status, len(written), len(failed), current, facts)
    return _run_dto(run, [_file_dto(f) for f in files])


def _record_failure(db: Session, trigger: str, actor: Optional[str], exc: Exception) -> ExportRun:
    now = _utcnow()
    run = RbacExportModel(
        id=audit.new_id(), trigger=trigger, actor=actor, status="failed", watermark=audit.watermark(db),
        change_summary={}, warnings=[], error=(str(exc).strip() or exc.__class__.__name__)[:1000],
        started_at=now, finished_at=now,
    )
    db.add(run)
    db.commit()
    return _run_dto(run, [])


def run_export(db: Session, *, trigger: str, actor: Optional[str] = None, force: bool = False) -> ExportRun:
    """Write every managed sheet to its configured path (single flight).

    Skipped (status ``skipped``) when the ledger watermark hasn't moved since the
    last completed export, unless ``force``; a partial or failed run is retried
    by the next scheduled run. A sheet whose path is blank is recorded as
    ``skipped`` with a warning. Archives each file's content on the run, and adds
    an ``rbac_exported`` fact to every request whose sheets the export writes for
    the first time. Raises ExportBusy while another export runs.
    """
    if trigger not in TRIGGERS:
        raise ValueError(f"Unknown export trigger: {trigger}")
    with _single_flight(db):
        try:
            return _export(db, trigger=trigger, actor=actor, force=force)
        except Exception as exc:
            logger.exception("Access rules export (%s) failed", trigger)
            db.rollback()
            try:
                return _record_failure(db, trigger, actor, exc)
            except Exception:  # noqa: BLE001
                logger.exception("Couldn't record the failed export")
                db.rollback()
                raise exc


# --- Status, download, path check ---------------------------------------------


def _target_sheet_order() -> Dict[str, int]:
    order: Dict[str, int] = {}
    for _, _, _, sheets in layout.export_targets():
        for sheet in sheets:
            order.setdefault(sheet, len(order))
    return order


def _files_by_run(db: Session, export_ids: Sequence[str]) -> Dict[str, List[ExportFile]]:
    if not export_ids:
        return {}
    F = RbacExportFileModel
    rows = (
        db.query(F.id, F.export_id, F.sheet, F.path, F.sha256, F.row_count, F.size_bytes, F.status, F.error,
                 F.content.isnot(None).label("has_content"))
        .filter(F.export_id.in_(list(export_ids)))
        .all()
    )
    order = _target_sheet_order()
    out: Dict[str, List[ExportFile]] = {}
    for r in sorted(rows, key=lambda r: (order.get(r.sheet, len(order)), r.sheet)):
        out.setdefault(r.export_id, []).append(ExportFile(
            id=r.id, sheet=r.sheet, path=r.path, sha256=r.sha256, row_count=r.row_count or 0,
            size_bytes=r.size_bytes or 0, status=r.status, error=r.error,
            downloadable=r.status == "written" and bool(r.has_content),
        ))
    return out


def _next_run_at(now: datetime) -> Optional[datetime]:
    cron_expr = (settings.RBAC_EXPORT_CRON or "").strip()
    if not cron_expr:
        return None
    cached = _next_export_time
    if cached is not None and cached > now:
        return cached
    try:
        return croniter(cron_expr, now).get_next(datetime)
    except (CroniterBadCronError, ValueError):
        return None


def status(db: Session, *, limit: int = 20) -> ExportStatus:
    runs = (
        db.query(RbacExportModel)
        .order_by(RbacExportModel.started_at.desc(), RbacExportModel.finished_at.desc())
        .limit(limit)
        .all()
    )
    files = _files_by_run(db, [r.id for r in runs])
    baseline = _last_complete_watermark(db)
    return ExportStatus(
        runs=[_run_dto(r, files.get(r.id, [])) for r in runs],
        cron=(settings.RBAC_EXPORT_CRON or "").strip(),
        next_run_at=_next_run_at(datetime.now(timezone.utc)),
        targets=[ExportTarget(key=k, label=lbl, path=p, sheets=list(s), configured=bool(p))
                 for k, lbl, p, s in layout.export_targets()],
        pending_changes=audit.watermark(db) > (baseline or 0),
    )


def get_file(db: Session, export_id: str, file_id: str) -> Optional[Tuple[str, bytes]]:
    """``(filename, content)`` of an archived export file, or None."""
    row = (
        db.query(RbacExportFileModel)
        .filter(RbacExportFileModel.id == file_id, RbacExportFileModel.export_id == export_id)
        .one_or_none()
    )
    if row is None or row.content is None:
        return None
    return f"{row.sheet}.csv", row.content.encode("utf-8")


def test_path(target: str) -> PathCheckResult:
    """Write and delete a probe file at a target's export path."""
    found = next(((k, lbl, p) for k, lbl, p, _ in layout.export_targets() if k == target), None)
    if found is None:
        return PathCheckResult(target=target, ok=False, message=f'"{target}" isn\'t an export target.')
    key, label, path = found
    if not path:
        return PathCheckResult(target=target, ok=False,
                               message=f"No export path is set for {_target_label(key, label)}, so its sheets aren't exported.")
    kind = _path_kind(path)
    if kind == "local" and not _local_allowed():
        return PathCheckResult(target=target, path=path, ok=False, message=LOCAL_PATH_REFUSED)

    folder = path.rstrip("/")
    probe = f"{folder}/.rbac_probe_{uuid.uuid4().hex}.txt"
    storage = _Storage()
    try:
        storage.ensure_dir(kind, folder)
        storage.write(kind, probe, b"Access rules export path check. Safe to delete.\n")
    except Exception as exc:  # noqa: BLE001
        logger.info("Export path check failed for %s (%s): %s", target, path, exc)
        return PathCheckResult(target=target, path=path, ok=False,
                               message=f"Couldn't write to {path}: {_describe_error(exc, kind)}")
    try:
        storage.delete(kind, probe)
    except Exception as exc:  # noqa: BLE001
        return PathCheckResult(target=target, path=path, ok=True,
                               message=(f"The app can write to {path}, but couldn't delete the probe file "
                                        f"{probe} ({_describe_error(exc, kind)}). Delete it by hand."))
    who = "The app" if kind == "local" else "The app's service principal"
    return PathCheckResult(target=target, path=path, ok=True, message=f"{who} can write to {path}.")


# --- Compare with a manual export ---------------------------------------------

_ENDING_NAMES = {
    "CRLF": "Windows line endings (CRLF)",
    "LF": "Unix line endings (LF)",
    "CR": "old Mac line endings (CR)",
    "mixed": "a mix of line endings",
    "none": "no line endings",
}


def _row_endings(text_: str) -> str:
    """The row terminator style outside quoted fields: CRLF, LF, CR, mixed or none."""
    counts: Counter = Counter()
    for match in _CSV_TOKEN.finditer(text_):
        token = match.group()
        if token == "\r\n":
            counts["CRLF"] += 1
        elif token == "\n":
            counts["LF"] += 1
        elif token == "\r":
            counts["CR"] += 1
    kinds = [k for k in ("CRLF", "LF", "CR") if counts[k]]
    if not kinds:
        return "none"
    return kinds[0] if len(kinds) == 1 else "mixed"


def _parse_csv(text_: str) -> List[List[str]]:
    return [row for row in csv.reader(io.StringIO(text_, newline=""))]


def _only_in(rows: Sequence[Sequence[str]], other: Sequence[Sequence[str]]) -> List[List[str]]:
    """Rows of ``rows`` not matched by a row of ``other`` (multiset difference, in order)."""
    remaining = Counter(tuple(r) for r in other)
    out: List[List[str]] = []
    for row in rows:
        key = tuple(row)
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            out.append(list(row))
    return out


def _show(cells: Sequence[str]) -> str:
    return " | ".join(cells) if cells else "(none)"


def _rows_are(n: int) -> str:
    return "1 row is" if n == 1 else f"{n} rows are"


def _normalized(text_: str) -> str:
    return text_.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def compare(db: Session, sheet: str, uploaded: bytes) -> CompareResult:
    """Compare what the export would write for ``sheet`` with a manually exported CSV."""
    name, _ = _layout(db, sheet)
    ours = render_sheet(db, name)
    result = CompareResult(
        sheet=name,
        identical=ours == uploaded,
        ours_sha256=hashlib.sha256(ours).hexdigest(),
        theirs_sha256=hashlib.sha256(uploaded).hexdigest(),
    )
    if result.identical:
        return result
    diffs: List[CompareDifference] = []

    ours_bom, theirs_bom = ours.startswith(BOM), uploaded.startswith(BOM)
    if theirs_bom and not ours_bom:
        diffs.append(CompareDifference(kind="bom", detail=(
            "The manual export starts with a byte-order mark and this export doesn't. "
            "Turn on the CSV byte-order mark setting to match.")))
    elif ours_bom and not theirs_bom:
        diffs.append(CompareDifference(kind="bom", detail=(
            "This export starts with a byte-order mark and the manual export doesn't. "
            "Turn off the CSV byte-order mark setting to match.")))
    ours_text = ours[len(BOM):].decode("utf-8") if ours_bom else ours.decode("utf-8")
    theirs_body = uploaded[len(BOM):] if theirs_bom else uploaded
    try:
        theirs_text = theirs_body.decode("utf-8")
    except UnicodeDecodeError:
        theirs_text = theirs_body.decode("cp1252", errors="replace")
        diffs.append(CompareDifference(kind="encoding", detail=(
            "The manual export isn't valid UTF-8; it looks like Windows-1252 (Excel's \"CSV (Comma delimited)\" "
            "format rather than \"CSV UTF-8\"). It was read as Windows-1252 to compare the rest. This export "
            "writes UTF-8, so a sheet with accented or other non-ASCII characters can't match byte for byte.")))

    ours_end, theirs_end = _row_endings(ours_text), _row_endings(theirs_text)
    if ours_end != theirs_end:
        hint = ""
        if theirs_end in ("CRLF", "LF"):
            hint = f" Set the CSV line ending setting to {theirs_end.lower()} to match."
        diffs.append(CompareDifference(kind="line_endings", detail=(
            f"This export uses {_ENDING_NAMES[ours_end]}; the manual export uses {_ENDING_NAMES[theirs_end]}.{hint}")))

    ours_trailing = ours_text.endswith(("\r", "\n"))
    theirs_trailing = theirs_text.endswith(("\r", "\n"))
    if ours_trailing and not theirs_trailing:
        diffs.append(CompareDifference(kind="trailing_newline", detail=(
            "The manual export has no line ending after its last row; this export ends every row, "
            "the last one included, with one.")))
    elif theirs_trailing and not ours_trailing:
        diffs.append(CompareDifference(kind="trailing_newline", detail=(
            "The manual export ends its last row with a line ending and this export doesn't.")))

    ours_rows, theirs_rows = _parse_csv(ours_text), _parse_csv(theirs_text)
    ours_header = ours_rows[0] if ours_rows else []
    theirs_header = theirs_rows[0] if theirs_rows else []
    if ours_header != theirs_header:
        diffs.append(CompareDifference(kind="header", detail=(
            f"The headers differ. This export: {_show(ours_header)}. Manual export: {_show(theirs_header)}.")))

    ours_data, theirs_data = ours_rows[1:], theirs_rows[1:]
    only_ours = _only_in(ours_data, theirs_data)
    only_theirs = _only_in(theirs_data, ours_data)
    if only_ours or only_theirs:
        diffs.append(CompareDifference(kind="rows", detail=(
            f"{_rows_are(len(only_ours))} only in this export, and "
            f"{_rows_are(len(only_theirs))} only in the manual export.")))
    elif ours_data != theirs_data:
        diffs.append(CompareDifference(kind="rows", detail=(
            "The rows are the same but in a different order.")))

    if ours_rows == theirs_rows:
        ours_lines = _normalized(ours_text).split("\n")
        theirs_lines = _normalized(theirs_text).split("\n")
        if ours_lines != theirs_lines:
            n = next((i for i, (a, b) in enumerate(zip(ours_lines, theirs_lines)) if a != b),
                     min(len(ours_lines), len(theirs_lines)))
            mine = ours_lines[n][:200] if n < len(ours_lines) else "(nothing)"
            theirs_line = theirs_lines[n][:200] if n < len(theirs_lines) else "(nothing)"
            diffs.append(CompareDifference(kind="quoting", detail=(
                "The cells are the same but some are quoted differently. "
                f"Line {n + 1} is {mine} here and {theirs_line} in the manual export.")))

    if not diffs:
        diffs.append(CompareDifference(kind="other", detail=(
            "The files differ in a way these checks don't explain. Compare them in a text editor.")))
    result.differences = diffs
    result.only_in_ours = only_ours[:COMPARE_ROWS_CAP]
    result.only_in_theirs = only_theirs[:COMPARE_ROWS_CAP]
    return result


# --- Scheduling ---------------------------------------------------------------


def request_export_soon() -> None:
    """Note that the ledger changed; with RBAC_EXPORT_ON_CHANGE on, the poller exports shortly after."""
    global _change_pending_since
    if not settings.RBAC_EXPORT_ON_CHANGE:
        return
    if _change_pending_since is None:
        _change_pending_since = datetime.now(timezone.utc)


def _scheduled_trigger(now: datetime) -> Optional[str]:
    """``schedule`` when the cron is due, ``on_change`` when a noted change has waited long enough."""
    global _next_export_time, _bad_cron, _change_pending_since
    cron_expr = (settings.RBAC_EXPORT_CRON or "").strip()
    if cron_expr:
        if _next_export_time is None:
            try:
                _next_export_time = croniter(cron_expr, now).get_next(datetime)
                _bad_cron = None
            except (CroniterBadCronError, ValueError):
                if _bad_cron != cron_expr:
                    logger.error("Invalid RBAC_EXPORT_CRON expression: %s", cron_expr)
                    _bad_cron = cron_expr
        if _next_export_time is not None and now >= _next_export_time:
            try:
                _next_export_time = croniter(cron_expr, now).get_next(datetime)
            except (CroniterBadCronError, ValueError):
                _next_export_time = None
            return "schedule"
    pending = _change_pending_since
    if pending is not None:
        if not settings.RBAC_EXPORT_ON_CHANGE:
            _change_pending_since = None
        elif (now - pending).total_seconds() >= ON_CHANGE_DELAY_SECONDS:
            return "on_change"
    return None


def _run_in_own_session(trigger: str) -> ExportRun:
    from app.db.session import get_lakebase_session

    db = get_lakebase_session()
    try:
        return run_export(db, trigger=trigger)
    finally:
        db.close()


async def rbac_export_task() -> None:
    """Poller hook: run a scheduled (or on-change) export when one is due."""
    global _change_pending_since
    if not (_yaml_config.get("features", {}) or {}).get("rbac", False):
        return
    trigger = _scheduled_trigger(datetime.now(timezone.utc))
    if trigger is None:
        return
    # This run covers every change noted so far; a change made during it is noted again.
    pending = _change_pending_since
    _change_pending_since = None
    try:
        result = await asyncio.to_thread(_run_in_own_session, trigger)
    except ExportBusy:
        logger.info("Skipped the %s access rules export: another export is running.", trigger)
        if pending is not None and _change_pending_since is None:
            _change_pending_since = pending
        return
    if result.status != "skipped":
        logger.info("Access rules %s export finished: %s", trigger, result.status)
