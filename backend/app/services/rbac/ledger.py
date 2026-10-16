"""The Access Rules ledger: reads, direct edits, access-change plan / apply, history.

Authorization is the caller's job (API / workflow tools, via ``approvers``);
this module enforces data rules: policy (``policy.evaluate``), optimistic
locking, limits and audit. Writes happen in one transaction per call and are
committed here.

Every write (direct edit, revert, approved request) goes through ``_write``:
one change set, one event per touched rule, and each update / delete as
``UPDATE ... WHERE id = :id AND version = :v``.

Access proposals: ``AccessProposal.outcomes[i].index`` and every finding's
``change_index`` index ``AccessProposal.items`` (the normalized items, with
added prerequisites, then any item that couldn't be resolved). In
``ApplyResult.skipped`` the index is into the approved proposal's ``items``.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.rbac import (
    PLANE_METASTORE,
    PLANE_UC,
    PLANE_WORKSPACE,
    RULE_ACTIVE,
    RULE_DELETED,
    SOURCE_IMPORT,
    SOURCE_REQUEST,
    SOURCE_REVERT,
    RbacChangeSetModel,
    RbacRuleEventModel,
    RbacRuleModel,
    RbacSheetModel,
)
from app.models.rbac import (
    CANONICAL_FIELDS,
    AccessChangeIn,
    AccessItem,
    AccessMatch,
    AccessPreview,
    AccessProposal,
    ApplyResult,
    ChangeSetDetail,
    ChangeSetIn,
    ChangeSetPreview,
    ChangeSetResult,
    ChangeSetSummary,
    FindAccessResult,
    Finding,
    HistoryPage,
    ItemOutcome,
    RowChange,
    RowDiff,
    Rule,
    RuleRow,
    RulesPage,
    SheetColumn,
    SheetInfo,
)
from app.services.rbac import audit, cells, inflight, layout, policy

logger = logging.getLogger(__name__)

# Duplicate-row lookups batch row hashes into IN lists of this size.
_HASH_CHUNK = 500


class LedgerError(Exception):
    """A change that can't be made, with a message fit for the user."""


class VersionConflict(LedgerError):
    """A row changed since the editor loaded it (HTTP 409)."""

    def __init__(self, rule_id: str, current: Optional[Rule]):
        super().__init__(f"Rule {rule_id} was changed by someone else. Reload and try again.")
        self.rule_id = rule_id
        self.current = current


class ChangeBlocked(LedgerError):
    """Policy denied the change (HTTP 422). ``preview`` carries the findings."""

    def __init__(self, preview: ChangeSetPreview):
        super().__init__("The change breaks an access policy.")
        self.preview = preview


# --- Small helpers -------------------------------------------------------------


@dataclass
class _Op:
    """One row edit, resolved against the current ledger, ready to preview and write."""

    op: str  # create | update | delete | restore
    sheet: str
    plane: str
    environment: Optional[str]
    rule_id: Optional[str] = None
    expected_version: Optional[int] = None
    # Current state (None for create) and target state (None for delete).
    before: Optional[RuleRow] = None
    after: Optional[RuleRow] = None
    # Index in the caller's input (the submitted change, or the reverted event).
    index: Optional[int] = None


@dataclass
class _Norm:
    """An access item after normalization, with anything that stops it being planned."""

    index: int
    item: AccessItem
    plane: Optional[str]
    findings: List[Finding] = field(default_factory=list)


def _cells_of(row: Any) -> Dict[str, str]:
    return {f: getattr(row, f) or "" for f in CANONICAL_FIELDS}


def _row_hash(row: RuleRow) -> str:
    return audit.compute_row_hash(row.sheet, _cells_of(row), row.extra)


def _snap(row: RuleRow) -> Dict[str, Any]:
    """A planned row in the same shape as ``audit.snapshot``."""
    data: Dict[str, Any] = _cells_of(row)
    data.update({
        "sheet": row.sheet,
        "plane": row.plane,
        "environment": row.environment,
        "position": row.position,
        "extra": dict(row.extra or {}),
        "status": row.status,
        "version": row.version,
    })
    return data


def _row_from_snapshot(
    snap: Optional[Dict[str, Any]],
    *,
    sheet: str,
    plane: str,
    environment: Optional[str],
    rule_id: Optional[str] = None,
) -> RuleRow:
    snap = snap or {}
    return RuleRow(
        id=rule_id or snap.get("id"),
        sheet=snap.get("sheet") or sheet,
        plane=snap.get("plane") or plane,
        environment=snap.get("environment") or environment,
        position=int(snap.get("position") or 0),
        extra={str(k): "" if v is None else str(v) for k, v in (snap.get("extra") or {}).items()},
        status=snap.get("status") or RULE_ACTIVE,
        version=int(snap.get("version") or 1),
        **{f: "" if snap.get(f) is None else str(snap.get(f)) for f in CANONICAL_FIELDS},
    )


def _diff_rows(diff: RowDiff) -> Tuple[Optional[RuleRow], Optional[RuleRow]]:
    """``(before, after)`` rows of a diff, as policy sees them (a delete has no after)."""
    kwargs = dict(sheet=diff.sheet, plane=diff.plane, environment=diff.environment, rule_id=diff.rule_id)
    before = _row_from_snapshot(diff.before, **kwargs) if diff.before else None
    after = _row_from_snapshot(diff.after, **kwargs) if diff.after and diff.op != "delete" else None
    return before, after


def _to_rule(model: RbacRuleModel, pending: Optional[Dict[str, List[str]]] = None) -> Rule:
    return Rule(
        **audit.to_row(model).model_dump(),
        source=model.source,
        source_ref=model.source_ref,
        created_at=model.created_at,
        created_by=model.created_by,
        updated_at=model.updated_at,
        updated_by=model.updated_by,
        pending_request_ids=list((pending or {}).get(model.id, [])),
    )


def _load(db: Session, rule_id: Optional[str]) -> Optional[RbacRuleModel]:
    if not rule_id:
        return None
    return db.get(RbacRuleModel, rule_id, populate_existing=True)


def _current_rule(db: Session, rule_id: str) -> Optional[Rule]:
    model = _load(db, rule_id)
    return _to_rule(model) if model is not None else None


def _max_position(db: Session, sheet: str) -> int:
    value = db.query(func.max(RbacRuleModel.position)).filter(RbacRuleModel.sheet == sheet).scalar()
    return int(value) if value is not None else 0


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _env_keys() -> str:
    return ", ".join(e.key for e in layout.environments()) or "none configured"


def _most_common(counter: Optional[Counter]) -> str:
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def _check_limit(count: int) -> None:
    limit = int(settings.RBAC_MAX_CHANGES_PER_COMMIT or 0)
    if limit > 0 and count > limit:
        raise LedgerError(
            f"A change set can hold at most {limit} changes and this one has {count}. "
            "Split it into smaller commits."
        )


def _export_soon() -> None:
    try:
        from app.services.rbac import csv_export

        csv_export.request_export_soon()
    except Exception as exc:  # the export is best-effort; the commit already happened
        logger.debug("Access Rules: could not request an export after a ledger change: %s", exc)


def _approver_group(plane: str) -> Optional[str]:
    try:
        from app.services.rbac import approvers

        group = approvers.approver_group(plane)
    except NotImplementedError:
        group = None
    return (group or "").strip() or None


# --- Reads -----------------------------------------------------------------------


def list_rules(
    db: Session,
    *,
    sheet: Optional[str] = None,
    plane: Optional[str] = None,
    environment: Optional[str] = None,
    q: Optional[str] = None,
    include_deleted: bool = False,
    offset: int = 0,
    limit: int = 500,
) -> RulesPage:
    """Rules in sheet + position order, with ``pending_request_ids`` filled from inflight."""
    query = db.query(RbacRuleModel)
    if sheet:
        spec = layout.sheet_spec(sheet)
        query = query.filter(RbacRuleModel.sheet == (spec.name if spec else sheet))
    if plane:
        query = query.filter(RbacRuleModel.plane == plane)
    if environment:
        query = query.filter(RbacRuleModel.environment == (layout.resolve_environment(environment) or environment))
    if not include_deleted:
        query = query.filter(RbacRuleModel.status == RULE_ACTIVE)
    needle = (q or "").strip()
    if needle:
        pattern = f"%{_escape_like(needle)}%"
        query = query.filter(or_(*[
            getattr(RbacRuleModel, f).ilike(pattern, escape="\\") for f in CANONICAL_FIELDS
        ]))
    total = query.count()
    models = (
        query.order_by(RbacRuleModel.sheet, RbacRuleModel.position, RbacRuleModel.id)
        .offset(max(int(offset or 0), 0))
        .limit(max(int(limit or 0), 0))
        .all()
    )
    pending = inflight.touched_rule_ids(db) if models else {}
    return RulesPage(rules=[_to_rule(m, pending) for m in models], total=total)


def get_rule(db: Session, rule_id: str) -> Optional[Rule]:
    model = db.get(RbacRuleModel, rule_id) if rule_id else None
    if model is None:
        return None
    return _to_rule(model, inflight.touched_rule_ids(db))


def _columns(spec: Optional[layout.SheetSpec], stored: Optional[RbacSheetModel]) -> List[SheetColumn]:
    if stored is not None and stored.columns:
        return [
            SheetColumn(header=str(c.get("header") or ""), field=c.get("field") or None)
            for c in stored.columns
            if isinstance(c, dict)
        ]
    plane = spec.plane if spec else (stored.plane if stored is not None else None)
    return [SheetColumn(**c) for c in layout.default_columns(plane)] if plane else []


def sheet_columns(db: Session, sheet: str) -> List[SheetColumn]:
    """The sheet's recorded layout (rbac_sheets), else the plane's default layout."""
    spec = layout.sheet_spec(sheet)
    name = spec.name if spec else sheet
    stored = db.get(RbacSheetModel, name) if name else None
    if spec is None and stored is None:
        raise LedgerError(f"'{sheet}' isn't one of the managed sheets.")
    return _columns(spec, stored)


def sheet_infos(db: Session, editable_planes: Set[str]) -> List[SheetInfo]:
    """Every managed sheet with its layout, active row count and export path."""
    counts = dict(
        db.query(RbacRuleModel.sheet, func.count(RbacRuleModel.id))
        .filter(RbacRuleModel.status == RULE_ACTIVE)
        .group_by(RbacRuleModel.sheet)
        .all()
    )
    stored = {s.name: s for s in db.query(RbacSheetModel).all()}
    editable = set(editable_planes or ())
    return [
        SheetInfo(
            name=spec.name,
            plane=spec.plane,
            environment=spec.environment,
            columns=_columns(spec, stored.get(spec.name)),
            row_count=int(counts.get(spec.name, 0)),
            export_target=spec.export_target,
            export_path=spec.export_path,
            editable=spec.plane in editable,
        )
        for spec in layout.managed_sheets()
    ]


def planes_in_change(db: Session, change: ChangeSetIn) -> Set[str]:
    """Planes a change set touches (for authorization). Raises LedgerError for unknown rows / sheets."""
    planes: Set[str] = set()
    for index, ch in enumerate(change.changes):
        if ch.op == "create":
            spec = layout.sheet_spec(ch.sheet)
            if spec is None:
                raise LedgerError(f"Change {index + 1}: '{ch.sheet or ''}' isn't one of the managed sheets.")
            planes.add(spec.plane)
        else:
            model = db.get(RbacRuleModel, ch.rule_id) if ch.rule_id else None
            if model is None:
                raise LedgerError(f"Change {index + 1}: rule {ch.rule_id or '(no id)'} doesn't exist.")
            planes.add(model.plane)
    return planes


# --- Previews: findings shared by direct edits and reverts ------------------------


def _pair(op: _Op) -> Tuple[Optional[RuleRow], Optional[RuleRow]]:
    if op.op == "create":
        return None, op.after
    if op.op == "delete":
        return op.before, None
    # update, and restore: a restore puts back atoms the row already had, so
    # policy compares against the deleted row rather than judging them as new.
    return op.before, op.after


def _policy_findings(ops: Sequence[_Op], *, reason: Optional[str]) -> List[Finding]:
    findings = policy.evaluate([_pair(op) for op in ops], path="admin_edit", reason=reason)
    out: List[Finding] = []
    for f in findings:
        idx = f.change_index
        if idx is not None and 0 <= idx < len(ops):
            op = ops[idx]
            f = f.model_copy(update={
                "change_index": op.index,
                "rule_id": f.rule_id or op.rule_id,
                "sheet": f.sheet or op.sheet,
            })
        out.append(f)
    return out


def _duplicate_findings(db: Session, ops: Sequence[_Op]) -> List[Finding]:
    """A warn for every after-row identical to another active row on its sheet (or in the set)."""
    touched = {op.rule_id for op in ops if op.rule_id}
    hashed = [(op, _row_hash(op.after)) for op in ops if op.op != "delete" and op.after is not None]
    if not hashed:
        return []
    by_sheet: Dict[str, Set[str]] = {}
    for op, digest in hashed:
        by_sheet.setdefault(op.sheet, set()).add(digest)
    existing: Dict[Tuple[str, str], str] = {}
    for sheet, digests in by_sheet.items():
        ordered = sorted(digests)
        for start in range(0, len(ordered), _HASH_CHUNK):
            chunk = ordered[start:start + _HASH_CHUNK]
            rows = (
                db.query(RbacRuleModel.id, RbacRuleModel.row_hash)
                .filter(RbacRuleModel.sheet == sheet, RbacRuleModel.status == RULE_ACTIVE)
                .filter(RbacRuleModel.row_hash.in_(chunk))
                .order_by(RbacRuleModel.position)
                .all()
            )
            for rule_id, digest in rows:
                if rule_id not in touched:
                    existing.setdefault((sheet, digest), rule_id)
    in_set = Counter((op.sheet, digest) for op, digest in hashed)
    out: List[Finding] = []
    for op, digest in hashed:
        if (op.sheet, digest) not in existing and in_set[(op.sheet, digest)] < 2:
            continue
        out.append(Finding(
            severity="warn",
            code="duplicate_row",
            message=f"This row is identical to another row on {op.sheet}.",
            sheet=op.sheet,
            rule_id=op.rule_id,
            change_index=op.index,
        ))
    return out


def _overlap_finding(request_ids: Sequence[str], *, sheet: str, rule_id: str,
                     change_index: Optional[int]) -> Finding:
    noun = "request" if len(request_ids) == 1 else "requests"
    return Finding(
        severity="warn",
        code="inflight_overlap",
        message=f"Open access {noun} {', '.join(request_ids)} will also change this row.",
        sheet=sheet,
        rule_id=rule_id,
        change_index=change_index,
    )


def _inflight_findings(db: Session, ops: Sequence[_Op]) -> List[Finding]:
    if not any(op.rule_id for op in ops):
        return []
    touched = inflight.touched_rule_ids(db)
    return [
        _overlap_finding(touched[op.rule_id], sheet=op.sheet, rule_id=op.rule_id, change_index=op.index)
        for op in ops
        if op.rule_id and touched.get(op.rule_id)
    ]


def _two_person(ops: Sequence[_Op]) -> bool:
    for op in ops:
        envs = {op.environment, op.before.environment if op.before else None}
        for key in envs:
            env = layout.environment(key)
            if env is not None and env.two_person_rule:
                return True
    return False


def _preview_diff(op: _Op) -> RowDiff:
    return RowDiff(
        op=op.op,
        rule_id=op.rule_id,
        sheet=op.sheet,
        plane=op.plane,
        environment=op.environment,
        before=_snap(op.before) if op.before is not None else None,
        after=_snap(op.after) if op.after is not None and op.op != "delete" else None,
    )


def _preview(db: Session, ops: Sequence[_Op], *, reason: Optional[str]) -> ChangeSetPreview:
    findings = _policy_findings(ops, reason=reason)
    findings += _duplicate_findings(db, ops)
    findings += _inflight_findings(db, ops)
    return ChangeSetPreview(
        diffs=[_preview_diff(op) for op in ops],
        findings=findings,
        blocked=policy.is_blocked(findings),
        two_person_required=_two_person(ops),
    )


# --- Direct edits ------------------------------------------------------------------


def _validate_keys(ch: RowChange, n: int) -> None:
    unknown = sorted(k for k in ch.cells if k not in CANONICAL_FIELDS)
    if unknown:
        raise LedgerError(f"Change {n}: unknown column {', '.join(unknown)}.")
    bad_extra = sorted(str(k) for k in (ch.extra or {}) if not str(k).isdigit())
    if bad_extra:
        raise LedgerError(
            f"Change {n}: extra columns are keyed by their column number, not {', '.join(bad_extra)}."
        )


def _same_content(a: RuleRow, b: RuleRow) -> bool:
    return (
        _cells_of(a) == _cells_of(b)
        and dict(a.extra or {}) == dict(b.extra or {})
        and a.environment == b.environment
    )


def _ops_for_change(db: Session, change: ChangeSetIn) -> List[_Op]:
    _check_limit(len(change.changes))
    ops: List[_Op] = []
    seen: Set[str] = set()
    next_position: Dict[str, int] = {}
    for index, ch in enumerate(change.changes):
        n = index + 1
        _validate_keys(ch, n)
        if ch.op == "create":
            if not (ch.sheet or "").strip():
                raise LedgerError(f"Change {n}: say which sheet the new row goes on.")
            spec = layout.sheet_spec(ch.sheet)
            if spec is None:
                raise LedgerError(f"Change {n}: '{ch.sheet}' isn't one of the managed sheets.")
            row_cells = {f: ch.cells.get(f, "") or "" for f in CANONICAL_FIELDS}
            env = spec.environment
            if env is None:
                env = layout.resolve_environment(row_cells["environment_value"])
            position = next_position.get(spec.name)
            if position is None:
                position = _max_position(db, spec.name) + 1
            next_position[spec.name] = position + 1
            after = RuleRow(
                sheet=spec.name, plane=spec.plane, environment=env, position=position,
                extra={str(k): v for k, v in (ch.extra or {}).items()},
                status=RULE_ACTIVE, version=1, **row_cells,
            )
            ops.append(_Op("create", spec.name, spec.plane, env, after=after, index=index))
            continue

        if not ch.rule_id or ch.version is None:
            raise LedgerError(f"Change {n}: to {ch.op} a row, send its id and the version you loaded.")
        if ch.rule_id in seen:
            raise LedgerError(f"Change {n}: rule {ch.rule_id} is changed more than once; combine the edits.")
        seen.add(ch.rule_id)
        model = _load(db, ch.rule_id)
        if model is None:
            raise LedgerError(f"Change {n}: rule {ch.rule_id} doesn't exist.")
        if model.version != ch.version:
            raise VersionConflict(ch.rule_id, _to_rule(model))
        before = audit.to_row(model)
        if ch.op == "delete":
            if model.status == RULE_DELETED:
                raise LedgerError(f"Change {n}: rule {ch.rule_id} is already deleted.")
            ops.append(_Op("delete", model.sheet, model.plane, model.environment, rule_id=model.id,
                           expected_version=ch.version, before=before, index=index))
            continue

        if model.status == RULE_DELETED:
            raise LedgerError(f"Change {n}: rule {ch.rule_id} is deleted and can't be edited.")
        extra = dict(before.extra or {})
        if ch.extra is not None:
            extra.update({str(k): v for k, v in ch.extra.items()})
        updates: Dict[str, Any] = {f: v or "" for f, v in ch.cells.items()}
        updates.update({"extra": extra, "version": before.version + 1})
        if model.plane == PLANE_METASTORE and "environment_value" in ch.cells:
            updates["environment"] = layout.resolve_environment(ch.cells["environment_value"])
        after = before.model_copy(update=updates)
        if _same_content(before, after):
            continue
        ops.append(_Op("update", model.sheet, model.plane, after.environment, rule_id=model.id,
                       expected_version=ch.version, before=before, after=after, index=index))
    return ops


def preview_change_set(db: Session, change: ChangeSetIn, *, actor: str) -> ChangeSetPreview:
    """Dry run of a direct edit: diffs, policy findings (path ``admin_edit``), two-person flag.

    Raises VersionConflict when a row's version doesn't match, LedgerError for
    an unknown row or sheet, or when the change exceeds RBAC_MAX_CHANGES_PER_COMMIT.
    """
    ops = _ops_for_change(db, change)
    return _preview(db, ops, reason=change.reason)


def preview_change_set_without_reason(db: Session, change: ChangeSetIn, *, actor: str) -> ChangeSetPreview:
    """``preview_change_set`` before the editor has written a reason: every check but its length."""
    return _preview(db, _ops_for_change(db, change), reason=None)


def _write(
    db: Session,
    ops: Sequence[_Op],
    *,
    actor: Optional[str],
    source: str,
    source_ref: Optional[str],
    reason: Optional[str],
) -> Tuple[RbacChangeSetModel, List[RowDiff]]:
    """Write ops as one change set with one event per rule. The caller commits or rolls back."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    change_set = audit.new_change_set(db, source=source, source_ref=source_ref, actor=actor, reason=reason)
    next_position: Dict[str, int] = {}
    counts: Counter = Counter()
    diffs: List[RowDiff] = []
    for op in ops:
        if op.op == "create":
            row = op.after
            position = next_position.get(op.sheet)
            if position is None:
                position = _max_position(db, op.sheet) + 1
            next_position[op.sheet] = position + 1
            row_cells = _cells_of(row)
            extra = dict(row.extra or {})
            model = RbacRuleModel(
                id=audit.new_id(),
                sheet=op.sheet,
                plane=op.plane,
                environment=op.environment,
                position=position,
                extra=extra,
                status=RULE_ACTIVE,
                version=1,
                row_hash=audit.compute_row_hash(op.sheet, row_cells, extra),
                source=source,
                source_ref=source_ref,
                created_at=now,
                created_by=actor,
                updated_at=now,
                updated_by=actor,
                **row_cells,
            )
            db.add(model)
            db.flush()
            after = audit.snapshot(model)
            audit.record_event(db, change_set, rule_id=model.id, action="create", before=None, after=after)
            diffs.append(RowDiff(op="create", rule_id=model.id, sheet=op.sheet, plane=op.plane,
                                 environment=op.environment, before=None, after=after))
            counts["created"] += 1
            continue

        current = _load(db, op.rule_id)
        if current is None:
            raise LedgerError(f"Rule {op.rule_id} doesn't exist.")
        if op.expected_version is None:
            raise LedgerError(f"Rule {op.rule_id} can't be changed without the version it was read at.")
        before = audit.snapshot(current)
        values: Dict[str, Any] = {}
        if op.op == "delete":
            values["status"] = RULE_DELETED
        else:
            row_cells = _cells_of(op.after)
            extra = dict(op.after.extra or {})
            values.update(row_cells)
            values["extra"] = extra
            values["environment"] = op.environment
            values["row_hash"] = audit.compute_row_hash(current.sheet, row_cells, extra)
            if op.op == "restore":
                values["status"] = RULE_ACTIVE
        values.update({"version": op.expected_version + 1, "updated_by": actor, "updated_at": now})
        updated = (
            db.query(RbacRuleModel)
            .filter(RbacRuleModel.id == op.rule_id, RbacRuleModel.version == op.expected_version)
            .update(values, synchronize_session=False)
        )
        if updated != 1:
            raise VersionConflict(op.rule_id, _current_rule(db, op.rule_id))
        after = audit.snapshot(_load(db, op.rule_id))
        audit.record_event(db, change_set, rule_id=op.rule_id, action=op.op, before=before, after=after)
        diffs.append(RowDiff(op=op.op, rule_id=op.rule_id, sheet=current.sheet, plane=current.plane,
                             environment=after.get("environment"), before=before,
                             after=None if op.op == "delete" else after))
        counts[{"update": "updated", "delete": "deleted", "restore": "restored"}[op.op]] += 1

    audit.finalize_change_set(
        change_set,
        created=counts["created"],
        updated=counts["updated"],
        deleted=counts["deleted"],
        restored=counts["restored"],
        sheets=[op.sheet for op in ops],
        planes=[op.plane for op in ops],
    )
    db.flush()
    return change_set, diffs


def commit_change_set(
    db: Session,
    change: ChangeSetIn,
    *,
    actor: str,
    source: str = "admin_edit",
    source_ref: Optional[str] = None,
) -> ChangeSetResult:
    """Apply a direct edit all-or-nothing, with a reason (RBAC_MIN_REASON_LENGTH).

    Each update / delete is ``UPDATE ... WHERE id = :id AND version = :v``; a
    mismatch rolls back and raises VersionConflict. Raises ChangeBlocked on a
    deny finding. Does not handle the two-person rule (the caller checks
    ``preview_change_set(...).two_person_required`` first).
    """
    try:
        ops = _ops_for_change(db, change)
        preview = _preview(db, ops, reason=change.reason)
        if preview.blocked:
            raise ChangeBlocked(preview)
        if not ops:
            db.rollback()
            return ChangeSetResult(findings=preview.findings)
        change_set, diffs = _write(db, ops, actor=actor, source=source, source_ref=source_ref,
                                   reason=change.reason)
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Access Rules: %s committed change set %s (%d rows, source %s)",
                actor, change_set.id, len(diffs), source)
    _export_soon()
    return ChangeSetResult(change_set_id=change_set.id, diffs=diffs, findings=preview.findings)


# --- History and revert ------------------------------------------------------------


def _summary(cs: RbacChangeSetModel) -> ChangeSetSummary:
    return ChangeSetSummary(
        id=cs.id,
        source=cs.source,
        source_ref=cs.source_ref,
        actor=cs.actor,
        reason=cs.reason,
        summary=dict(cs.summary or {}),
        reverted_by=cs.reverted_by,
        created_at=cs.created_at,
    )


def _events(db: Session, change_set_id: str) -> List[RbacRuleEventModel]:
    return (
        db.query(RbacRuleEventModel)
        .filter(RbacRuleEventModel.change_set_id == change_set_id)
        .order_by(RbacRuleEventModel.id)
        .all()
    )


def _event_diff(db: Session, event: RbacRuleEventModel) -> RowDiff:
    snap = event.after or event.before or {}
    sheet, plane = snap.get("sheet"), snap.get("plane")
    if not sheet or not plane:
        model = db.get(RbacRuleModel, event.rule_id)
        sheet = sheet or (model.sheet if model else "")
        plane = plane or (model.plane if model else PLANE_UC)
    action = event.action if event.action in ("create", "update", "delete", "restore") else "update"
    return RowDiff(
        op=action,
        rule_id=event.rule_id,
        sheet=sheet,
        plane=plane,
        environment=snap.get("environment"),
        before=event.before,
        after=None if action == "delete" else event.after,
    )


def history(db: Session, *, offset: int = 0, limit: int = 50) -> HistoryPage:
    query = db.query(RbacChangeSetModel)
    total = query.count()
    rows = (
        query.order_by(RbacChangeSetModel.created_at.desc(), RbacChangeSetModel.id.desc())
        .offset(max(int(offset or 0), 0))
        .limit(max(int(limit or 0), 0))
        .all()
    )
    return HistoryPage(change_sets=[_summary(cs) for cs in rows], total=total)


def change_set_detail(db: Session, change_set_id: str) -> Optional[ChangeSetDetail]:
    cs = db.get(RbacChangeSetModel, change_set_id) if change_set_id else None
    if cs is None:
        return None
    return ChangeSetDetail(
        **_summary(cs).model_dump(),
        diffs=[_event_diff(db, e) for e in _events(db, cs.id)],
    )


def _event_version(event: RbacRuleEventModel) -> Optional[int]:
    """The version the event left the rule at (what the rule must still be at to revert)."""
    if event.after and event.after.get("version") is not None:
        return int(event.after["version"])
    if event.before and event.before.get("version") is not None:
        return int(event.before["version"]) + 1
    return None


def _revert_ops(db: Session, change_set_id: str) -> Tuple[RbacChangeSetModel, List[_Op]]:
    cs = db.get(RbacChangeSetModel, change_set_id) if change_set_id else None
    if cs is None:
        raise LedgerError("That change set doesn't exist.")
    if cs.reverted_by:
        raise LedgerError(f"This change set was already reverted by change set {cs.reverted_by}.")
    events = _events(db, cs.id)
    if not events:
        raise LedgerError("This change set didn't change any rows, so there's nothing to revert.")
    # Resets must stay revertible however many rows they touched.
    if cs.source != SOURCE_IMPORT:
        _check_limit(len(events))
    ops: List[_Op] = []
    for index, event in enumerate(events):
        model = _load(db, event.rule_id)
        if model is None:
            raise LedgerError(f"Rule {event.rule_id} no longer exists, so this change set can't be reverted.")
        expected = _event_version(event)
        if expected is None or model.version != expected:
            raise VersionConflict(event.rule_id, _to_rule(model))
        current = audit.to_row(model)
        if event.action in ("create", "restore"):
            ops.append(_Op("delete", model.sheet, model.plane, model.environment, rule_id=model.id,
                           expected_version=model.version, before=current, index=index))
            continue
        prior = event.before or {}
        target: Dict[str, Any] = {f: "" if prior.get(f) is None else str(prior.get(f)) for f in CANONICAL_FIELDS}
        target["extra"] = {str(k): "" if v is None else str(v) for k, v in (prior.get("extra") or {}).items()}
        target["environment"] = prior.get("environment", model.environment)
        target["version"] = model.version + 1
        if event.action == "delete":
            target["status"] = RULE_ACTIVE
            op_name = "restore"
        else:
            op_name = "update"
        after = current.model_copy(update=target)
        ops.append(_Op(op_name, model.sheet, model.plane, after.environment, rule_id=model.id,
                       expected_version=model.version, before=current, after=after, index=index))
    return cs, ops


def revert_preview(
    db: Session, change_set_id: str, *, actor: str, reason: Optional[str] = None,
) -> ChangeSetPreview:
    """The inverse change set's diffs and findings (rows changed since are a VersionConflict).

    Pass ``reason`` to check it as ``revert_change_set`` will; without one it isn't checked.
    """
    _, ops = _revert_ops(db, change_set_id)
    return _preview(db, ops, reason=reason)


def revert_change_set(db: Session, change_set_id: str, *, actor: str, reason: str) -> ChangeSetResult:
    """Record the inverse of a change set as a new change set (source ``revert``)."""
    try:
        original, ops = _revert_ops(db, change_set_id)
        preview = _preview(db, ops, reason=reason)
        if preview.blocked:
            raise ChangeBlocked(preview)
        change_set, diffs = _write(db, ops, actor=actor, source=SOURCE_REVERT, source_ref=original.id,
                                   reason=reason)
        original.reverted_by = change_set.id
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Access Rules: %s reverted change set %s as %s", actor, change_set_id, change_set.id)
    _export_soon()
    return ChangeSetResult(change_set_id=change_set.id, diffs=diffs, findings=preview.findings)


# --- Access changes: normalize, plan, apply -----------------------------------------


def _deny(code: str, message: str, item: AccessItem, **kwargs: Any) -> Finding:
    return Finding(severity="deny", code=code, message=message,
                   principal=item.principal_name or None, **kwargs)


def _normalize_item(index: int, raw: AccessItem) -> _Norm:
    securable_type = layout.normalize_securable_type(raw.securable_type)
    # The securable type alone decides the type of rule, and so the sheet and approvers.
    plane = layout.plane_for_securable_type(securable_type)
    env_key = layout.resolve_environment(raw.environment)
    privileges: List[str] = []
    for token in raw.privileges:
        for part in cells.split_cell(token):
            canonical = policy.canonical_privilege(securable_type, part)
            if canonical and canonical not in privileges:
                privileges.append(canonical)
    item = AccessItem(
        action=raw.action,
        environment=env_key or ((raw.environment or "").strip() or None),
        securable_type=securable_type,
        catalog=(raw.catalog or "").strip(),
        schema_name=(raw.schema_name or "").strip(),
        object_name=(raw.object_name or "").strip(),
        workspace_name=(raw.workspace_name or "").strip(),
        principal_type=cells.principal_type_key(raw.principal_type) or "group",
        principal_name=(raw.principal_name or "").strip(),
        privileges=privileges or list(raw.privileges),
        plane=plane,
    )
    norm = _Norm(index=index, item=item, plane=plane)
    if plane is None:
        norm.findings.append(_deny(
            "unknown_securable_type",
            f"'{raw.securable_type}' isn't a securable type access rules cover. Use a type such as "
            "catalog, schema, table, view, connection, workspace or workspace entitlement.",
            item, field="securable_type",
        ))
        return norm
    if raw.plane and raw.plane != plane:
        norm.findings.append(_deny(
            "plane_mismatch",
            f"A {securable_type} is a {layout.PLANE_LABELS.get(plane, plane)} rule, not "
            f"{layout.PLANE_LABELS.get(raw.plane, raw.plane)}.",
            item, field="securable_type",
        ))
    if env_key is None and (plane != PLANE_METASTORE or (raw.environment or "").strip()):
        if (raw.environment or "").strip():
            message = f"'{raw.environment}' isn't a configured environment. Use one of: {_env_keys()}."
        else:
            message = f"Say which environment this is for: {_env_keys()}."
        norm.findings.append(_deny("unknown_environment", message, item, field="environment"))
    elif layout.sheet_for(plane, env_key) is None:
        norm.findings.append(_deny(
            "unknown_environment",
            f"No sheet holds {layout.PLANE_LABELS.get(plane, plane)} rules for environment '{env_key}'.",
            item, field="environment",
        ))
    if not item.principal_name:
        norm.findings.append(_deny("missing_principal", "Name the group to grant or revoke access for.",
                                   item, field="principal_name"))
    if not privileges:
        norm.findings.append(_deny("missing_privilege", "Name at least one privilege.", item, field="privilege"))
    return norm


def _active_rows(db: Session, plane: str) -> Tuple[List[RuleRow], Dict[str, int]]:
    names = [s.name for s in layout.sheets_for_plane(plane)]
    if not names:
        return [], {}
    models = (
        db.query(RbacRuleModel)
        .filter(RbacRuleModel.sheet.in_(names), RbacRuleModel.status == RULE_ACTIVE)
        .order_by(RbacRuleModel.sheet, RbacRuleModel.position, RbacRuleModel.id)
        .all()
    )
    return [audit.to_row(m) for m in models], {m.id: m.version for m in models}


class _Planner:
    """``cells.plan_edits`` against one plane's active rows, with owner / environment defaults."""

    def __init__(self, plane: str, rows: List[RuleRow]):
        self.plane = plane
        self.rows = rows
        self._env_values: Dict[str, Counter] = {}
        self._owners: Dict[str, Counter] = {}
        self._catalog_owners: Dict[Tuple[str, str], Counter] = {}
        for row in rows:
            if row.environment_value:
                self._env_values.setdefault(row.sheet, Counter())[row.environment_value] += 1
            if row.owner:
                self._owners.setdefault(row.sheet, Counter())[row.owner] += 1
                key = (row.sheet, row.catalog.strip().lower())
                self._catalog_owners.setdefault(key, Counter())[row.owner] += 1

    def sheet_for(self, item: AccessItem) -> Optional[str]:
        spec = layout.sheet_for(self.plane, item.environment)
        return spec.name if spec else None

    def owner_for(self, item: AccessItem) -> str:
        if self.plane == PLANE_WORKSPACE:
            return ""
        default = (settings.RBAC_DEFAULT_OWNER or "").strip()
        if default:
            return default
        sheet = self.sheet_for(item)
        return (
            _most_common(self._catalog_owners.get((sheet, item.catalog.strip().lower())))
            or _most_common(self._owners.get(sheet))
        )

    def environment_value_for(self, item: AccessItem) -> str:
        if self.plane == PLANE_METASTORE:
            return item.environment or ""
        return _most_common(self._env_values.get(self.sheet_for(item))) or (item.environment or "")

    def plan(self, items: Sequence[AccessItem]) -> Tuple[List[RowDiff], List[ItemOutcome]]:
        if not items:
            return [], []
        diffs, outcomes = cells.plan_edits(
            self.rows,
            list(items),
            sheet_for_item=self.sheet_for,
            owner_for_item=self.owner_for,
            environment_value_for_item=self.environment_value_for,
        )
        for diff in diffs:
            if diff.environment is None:
                spec = layout.sheet_spec(diff.sheet)
                if spec is not None and spec.environment:
                    diff.environment = spec.environment
                else:
                    snap = diff.after or diff.before or {}
                    diff.environment = layout.resolve_environment(snap.get("environment_value"))
        return list(diffs), list(outcomes)


def _finding_matches(finding: Finding, item: AccessItem, sheet: Optional[str]) -> bool:
    """Whether a plan finding is about this item (a finding naming no principal is about every item)."""
    if finding.principal and finding.principal.strip().lower() != item.principal_name.lower():
        return False
    if finding.privilege and finding.privilege.strip().upper() not in {p.upper() for p in item.privileges}:
        return False
    if finding.sheet and sheet and finding.sheet != sheet:
        return False
    return True


def _plan_plane(
    db: Session,
    plane: str,
    members: Sequence[_Norm],
    *,
    justification: Optional[str],
    touched: Dict[str, List[str]],
) -> AccessProposal:
    rows, _ = _active_rows(db, plane)
    planner = _Planner(plane, rows)
    plannable = [m for m in members if not m.findings]
    unresolved = [m for m in members if m.findings]

    items, added = cells.add_prerequisites([m.item for m in plannable], rows)
    diffs, raw_outcomes = planner.plan(items)

    outcomes: Dict[int, ItemOutcome] = {o.index: o for o in raw_outcomes}
    policy_findings = policy.evaluate([_diff_rows(d) for d in diffs], path="request",
                                      justification=justification)
    findings: List[Finding] = []
    for f in policy_findings:
        targets = [i for i, item in enumerate(items) if _finding_matches(f, item, planner.sheet_for(item))]
        findings.append(f.model_copy(update={"change_index": targets[0] if len(targets) == 1 else None}))
        if f.severity != "deny":
            continue
        for i in targets:
            current = outcomes.get(i)
            if current is None:
                outcomes[i] = ItemOutcome(index=i, item=items[i], status="blocked", reason=f.message)
            elif current.status == "change":
                outcomes[i] = current.model_copy(update={"status": "blocked", "reason": f.message})

    all_items = list(items)
    for member in unresolved:
        index = len(all_items)
        all_items.append(member.item)
        outcomes[index] = ItemOutcome(index=index, item=member.item, status="blocked",
                                      reason=member.findings[0].message)
        findings.extend(f.model_copy(update={"change_index": index}) for f in member.findings)

    for diff in diffs:
        if diff.rule_id and touched.get(diff.rule_id):
            findings.append(_overlap_finding(touched[diff.rule_id], sheet=diff.sheet, rule_id=diff.rule_id,
                                             change_index=None))

    group = _approver_group(plane)
    return AccessProposal(
        plane=plane,
        approver_group=group,
        approver_fallback=group is None,
        items=all_items,
        added_prerequisites=added,
        outcomes=[outcomes[i] for i in sorted(outcomes)],
        diffs=diffs,
        findings=findings,
        blocked=policy.is_blocked(findings),
        report_markdown="",
    )


def plan_access_change(db: Session, change: AccessChangeIn, *, requester: Optional[str]) -> AccessPreview:
    """One proposal per plane the items touch, each with diffs, outcomes and findings.

    Resolves environments (aliases) and planes, adds prerequisites, plans row
    edits with ``cells.plan_edits`` against the current ledger and evaluates
    policy (path ``request``) including the justification. Sets ``group_ref``
    when there is more than one proposal. ``report_markdown`` is left empty
    (the workflow's plan step renders it).

    An item whose type can't be resolved joins the first proposal (or a ``uc``
    one when nothing resolves) as a blocked outcome with a deny finding.
    """
    norms = [_normalize_item(i, item) for i, item in enumerate(change.items)]
    planes: List[str] = []
    for norm in norms:
        if norm.plane and norm.plane not in planes:
            planes.append(norm.plane)
    if not planes:
        planes.append(PLANE_UC)
    touched = inflight.touched_rule_ids(db)
    proposals = []
    for k, plane in enumerate(planes):
        members = [n for n in norms if n.plane == plane or (n.plane is None and k == 0)]
        proposals.append(_plan_plane(db, plane, members, justification=change.justification, touched=touched))
    logger.debug("Access Rules: planned %d item(s) for %s across %s", len(change.items), requester, planes)
    return AccessPreview(group_ref=audit.new_id() if len(proposals) > 1 else None, proposals=proposals)


AtomKey = Tuple[str, tuple, str, str, str]


def _row_atom_keys(snap: Optional[Dict[str, Any]], diff: RowDiff) -> Set[Tuple[str, tuple, str, str]]:
    if not snap or (snap.get("status") or RULE_ACTIVE) != RULE_ACTIVE:
        return set()
    row = _row_from_snapshot(snap, sheet=diff.sheet, plane=diff.plane, environment=diff.environment)
    securable = cells.securable_key(row, row.plane)
    return {
        (cells.cell_key(row.sheet), securable, cells.cell_key(a.principal), cells.cell_key(a.privilege))
        for a in cells.atoms(row)
    }


def _diff_atoms(diff: RowDiff) -> Set[AtomKey]:
    before = _row_atom_keys(diff.before, diff)
    after = set() if diff.op == "delete" else _row_atom_keys(diff.after, diff)
    return {(*k, "+") for k in after - before} | {(*k, "-") for k in before - after}


def _atoms_of(diffs: Iterable[RowDiff]) -> Set[AtomKey]:
    out: Set[AtomKey] = set()
    for diff in diffs:
        out |= _diff_atoms(diff)
    return out


def _op_from_diff(diff: RowDiff, versions: Dict[str, int]) -> _Op:
    before, after = _diff_rows(diff)
    expected = None
    environment = diff.environment
    if diff.op != "create":
        if diff.rule_id not in versions:
            raise LedgerError(f"Rule {diff.rule_id} is no longer active, so the plan can't be applied.")
        expected = versions[diff.rule_id]
        if before is not None:
            environment = before.environment
    return _Op(diff.op, diff.sheet, diff.plane, environment, rule_id=diff.rule_id,
               expected_version=expected, before=before, after=after)


def _noop_reason(item: AccessItem) -> str:
    return "already in place" if item.action == "grant" else "already removed"


def apply_access_change(db: Session, *, request_id: str, approved: AccessProposal, actor: str) -> ApplyResult:
    """Re-plan ``approved.items`` against the current ledger and apply only the
    intersection with ``approved.diffs`` (never wider). Idempotent on
    ``request_id``: a change set with ``source_ref == request_id`` already
    present returns ``already_applied``. Raises ChangeBlocked if policy now denies.

    Prerequisites are not re-added: ``approved.items`` already carries them,
    and any new one would fall outside the approved plan.
    """
    existing = (
        db.query(RbacChangeSetModel)
        .filter(RbacChangeSetModel.source == SOURCE_REQUEST, RbacChangeSetModel.source_ref == request_id)
        .order_by(RbacChangeSetModel.created_at)
        .first()
    )
    if existing is not None:
        detail = change_set_detail(db, existing.id)
        return ApplyResult(change_set_id=existing.id, applied=detail.diffs if detail else [],
                           already_applied=True)

    try:
        plane = approved.plane
        norms = [_normalize_item(i, item) for i, item in enumerate(approved.items)]
        problems = [f for n in norms for f in n.findings]
        for norm in norms:
            if norm.plane is not None and norm.plane != plane:
                problems.append(_deny("mixed_planes", "This item belongs to a different type of request.",
                                      norm.item))
        if problems:
            raise ChangeBlocked(ChangeSetPreview(diffs=[], findings=problems, blocked=True))

        rows, versions = _active_rows(db, plane)
        planner = _Planner(plane, rows)
        approved_atoms = _atoms_of(approved.diffs)

        kept: List[_Norm] = []
        skipped: List[ItemOutcome] = []
        for norm in norms:
            diffs_i, outcomes_i = planner.plan([norm.item])
            status = outcomes_i[0].status if outcomes_i else ("change" if diffs_i else "noop")
            if status == "noop" or not diffs_i:
                skipped.append(ItemOutcome(index=norm.index, item=norm.item, status="noop",
                                           reason=_noop_reason(norm.item)))
            elif status == "blocked":
                skipped.append(ItemOutcome(index=norm.index, item=norm.item, status="blocked",
                                           reason=outcomes_i[0].reason))
            elif not _atoms_of(diffs_i) <= approved_atoms:
                skipped.append(ItemOutcome(index=norm.index, item=norm.item, status="noop",
                                           reason="outside the approved plan"))
            else:
                kept.append(norm)

        diffs, outcomes = planner.plan([n.item for n in kept])
        for outcome in outcomes:
            if outcome.status != "change" and 0 <= outcome.index < len(kept):
                norm = kept[outcome.index]
                skipped.append(ItemOutcome(index=norm.index, item=norm.item, status="noop",
                                           reason=_noop_reason(norm.item)))
        if not _atoms_of(diffs) <= approved_atoms:
            raise LedgerError("The approved plan no longer applies cleanly. Submit the request again.")

        findings = policy.evaluate([_diff_rows(d) for d in diffs], path="request")
        if policy.is_blocked(findings):
            raise ChangeBlocked(ChangeSetPreview(diffs=diffs, findings=findings, blocked=True))

        skipped.sort(key=lambda o: o.index)
        if not diffs:
            db.rollback()
            return ApplyResult(skipped=skipped)
        ops = [_op_from_diff(d, versions) for d in diffs]
        change_set, written = _write(db, ops, actor=actor, source=SOURCE_REQUEST, source_ref=request_id,
                                     reason=f"Approved access request {request_id}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Access Rules: applied request %s as change set %s (%d rows, %d skipped)",
                request_id, change_set.id, len(written), len(skipped))
    _export_soon()
    return ApplyResult(change_set_id=change_set.id, applied=written, skipped=skipped)


# --- Find access ---------------------------------------------------------------------


def find_access(
    db: Session,
    *,
    securable_type: Optional[str] = None,
    catalog: Optional[str] = None,
    schema_name: Optional[str] = None,
    object_name: Optional[str] = None,
    workspace_name: Optional[str] = None,
    principal_name: Optional[str] = None,
    environment: Optional[str] = None,
    limit: int = 50,
) -> FindAccessResult:
    """Who holds what on a securable (including parent catalog / schema rows), or what a principal holds.

    Metastore rows with no environment match any ``environment`` filter.
    """
    stype = layout.normalize_securable_type(securable_type) if (securable_type or "").strip() else None
    fields = {
        "catalog": (catalog or "").strip(),
        "schema_name": (schema_name or "").strip(),
        "object_name": (object_name or "").strip(),
        "workspace_name": (workspace_name or "").strip(),
    }
    fields = {k: v for k, v in fields.items() if v}
    principal = (principal_name or "").strip()
    if not stype and not fields and not principal:
        raise LedgerError("Say which securable or which group to look up.")

    base = db.query(RbacRuleModel).filter(RbacRuleModel.status == RULE_ACTIVE)
    if (environment or "").strip():
        env_key = layout.resolve_environment(environment)
        if env_key is None:
            raise LedgerError(f"'{environment}' isn't a configured environment. Use one of: {_env_keys()}.")
        base = base.filter(or_(
            RbacRuleModel.environment == env_key,
            and_(RbacRuleModel.plane == PLANE_METASTORE, RbacRuleModel.environment.is_(None)),
        ))
    if principal:
        base = base.filter(RbacRuleModel.principal_name.ilike(f"%{_escape_like(principal)}%", escape="\\"))
    order = (RbacRuleModel.sheet, RbacRuleModel.position, RbacRuleModel.id)

    direct_query = base
    for name, value in fields.items():
        direct_query = direct_query.filter(func.lower(getattr(RbacRuleModel, name)) == value.lower())
    direct = [
        m for m in direct_query.order_by(*order).all()
        if stype is None or layout.normalize_securable_type(m.securable_type) == stype
    ]

    parents: List[RbacRuleModel] = []
    cat, sch, obj = fields.get("catalog"), fields.get("schema_name"), fields.get("object_name")
    uc_query = "workspace_name" not in fields and (stype is None or layout.SECURABLE_TYPES.get(stype) == PLANE_UC)
    if uc_query and cat:
        want_catalog = bool(sch or obj) or stype not in (None, "catalog")
        want_schema = bool(sch) and (bool(obj) or stype not in (None, "catalog", "schema"))
        direct_ids = {m.id for m in direct}
        candidates = (
            base.filter(RbacRuleModel.plane == PLANE_UC, func.lower(RbacRuleModel.catalog) == cat.lower())
            .order_by(*order)
            .all()
        )
        for m in candidates:
            if m.id in direct_ids:
                continue
            kind = layout.normalize_securable_type(m.securable_type)
            if want_catalog and kind == "catalog":
                parents.append(m)
            elif want_schema and kind == "schema" and (m.schema_name or "").strip().lower() == sch.lower():
                parents.append(m)

    matches: List[AccessMatch] = []
    for via, models in (("direct", direct), ("parent", parents)):
        for m in models:
            principals = cells.split_cell(m.principal_name)
            if principal:
                principals = [p for p in principals if p.lower() == principal.lower()]
            privileges = cells.split_cell(m.privilege)
            for p in principals:
                matches.append(AccessMatch(
                    rule_id=m.id,
                    sheet=m.sheet,
                    plane=m.plane,
                    environment=m.environment,
                    securable_type=m.securable_type or "",
                    catalog=m.catalog or "",
                    schema_name=m.schema_name or "",
                    object_name=m.object_name or "",
                    workspace_name=m.workspace_name or "",
                    principal_type=m.principal_type or "",
                    principal_name=p,
                    privileges=privileges,
                    via=via,
                ))
    cap = max(int(limit or 0), 1)
    return FindAccessResult(matches=matches[:cap], truncated=len(matches) > cap)
