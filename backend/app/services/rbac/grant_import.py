"""Bulk SELECT grants from a two-column CSV (ADR 0007).

Column A names a Unity Catalog schema (``catalog.schema`` or ``catalog.schema.*``)
or view (``catalog.schema.view``); column B names one group. Every line is a
``SELECT`` grant to that group. The environment comes from the catalog: the
environment whose Catalogs setting lists it, else the one environment a whole
``_``-separated segment of its name matches. Lines are planned with the same
planner a request uses (ADR 0005 D1), so groups are appended to existing packed
rows and ``USE_CATALOG`` / ``USE_SCHEMA`` are added where missing. A view whose
group already reads its schema or catalog is reported as covered, and not written.

Nothing is stored at preview. The caller saves the change set through the
direct-edit path, after ``planned_change`` confirms the plan hasn't changed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.rbac import PLANE_UC, RULE_ACTIVE
from app.models.rbac import (
    AccessItem,
    ChangeSetIn,
    GrantImportLine,
    GrantImportPreview,
    RuleRow,
)
from app.services.rbac import cells, layout, ledger
from app.services.rbac.ledger import LedgerError

logger = logging.getLogger(__name__)

MAX_LINES = 5000

SELECT = "SELECT"
SCHEMA = "schema"
VIEW = "view"

CHANGE = "change"
ALREADY_HELD = "already_held"
COVERED = "covered"
DUPLICATE = "duplicate"
REJECTED = "rejected"

SETTINGS_HINT = "List the catalog under an environment's Catalogs in Admin → Settings → Access Rules."


class GrantFileUnreadable(LedgerError):
    """The upload isn't a CSV this import can read."""


@dataclass
class _Line:
    out: GrantImportLine
    catalog: str = ""
    schema: str = ""
    obj: str = ""
    item: Optional[AccessItem] = None

    def reject(self, message: str) -> None:
        self.out.status = REJECTED
        self.out.message = message


# --- Reading the file -------------------------------------------------------------


def _decode(data: bytes) -> str:
    # Excel writes UTF-8 (with a BOM) for "CSV UTF-8" and the Windows code page for plain "CSV".
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise GrantFileUnreadable("The file isn't readable text. Save it from Excel as CSV UTF-8 and try again.")


def _records(data: bytes) -> List[List[str]]:
    text = _decode(data)
    if "\x00" in text:
        raise GrantFileUnreadable("That file isn't a CSV. Save the sheet as CSV and try again.")
    try:
        return [[cell.strip() for cell in record] for record in csv.reader(io.StringIO(text, newline=""))]
    except csv.Error as exc:
        raise GrantFileUnreadable(f"The CSV couldn't be read: {exc}.") from exc


def parse_securable(text: str) -> Tuple[Optional[Tuple[str, str, str, str]], str]:
    """``((kind, catalog, schema, object), "")`` for column A, or ``(None, why it's rejected)``."""
    raw = (text or "").strip()
    if not raw:
        return None, "Column A is empty."
    parts = [p.strip().strip("`").strip() for p in raw.split(".")]
    if len(parts) == 2 and parts[1] == "*":
        return None, "Grants on a whole catalog aren't supported. Name a schema (catalog.schema) or a view."
    if len(parts) == 3 and parts[2] == "*":
        parts = parts[:2]
    if len(parts) == 1:
        return None, "This names only a catalog. Use catalog.schema, catalog.schema.* or catalog.schema.view."
    if len(parts) > 3:
        return None, "This has more than three parts. Use catalog.schema, catalog.schema.* or catalog.schema.view."
    if any(not p for p in parts):
        return None, "One of the name's parts is empty."
    if any("*" in p for p in parts):
        return None, "A * can only end the name, as in catalog.schema.*."
    if len(parts) == 2:
        return (SCHEMA, parts[0], parts[1], ""), ""
    return (VIEW, parts[0], parts[1], parts[2]), ""


# --- The environment, from the catalog ---------------------------------------------


def _existing_environments(rows: Sequence[RuleRow]) -> Dict[str, Set[str]]:
    """Catalog (lowercase) -> the environments of the UC sheets that already name it."""
    out: Dict[str, Set[str]] = {}
    for row in rows:
        if row.status == RULE_ACTIVE and row.catalog.strip() and row.environment:
            out.setdefault(cells.cell_key(row.catalog), set()).add(row.environment)
    return out


def environment_for_catalog(catalog: str, existing: Dict[str, Set[str]]) -> Tuple[Optional[str], str]:
    """``(environment key, "")`` for a catalog, or ``(None, why it can't be told)`` (ADR 0007 D1)."""
    key = cells.cell_key(catalog)
    listed = [env.key for env in layout.environments() if key in {cells.cell_key(c) for c in env.catalogs}]
    if len(listed) > 1:
        return None, (f"The catalog {catalog} is listed under the Catalogs setting of more than one environment "
                      f"({', '.join(listed)}).")
    if listed:
        env_key = listed[0]
    else:
        found: List[str] = []
        for segment in key.split("_"):
            match = layout.resolve_environment(segment) if segment else None
            if match and match not in found:
                found.append(match)
        if not found:
            return None, f"No part of the catalog name {catalog} names an environment. {SETTINGS_HINT}"
        if len(found) > 1:
            return None, (f"The catalog name {catalog} names more than one environment ({', '.join(found)}). "
                          f"{SETTINGS_HINT}")
        env_key = found[0]
    others = existing.get(key) or set()
    if others and env_key not in others:
        return None, (f"The catalog {catalog} resolves to {env_key}, but its existing rules are on the "
                      f"{', '.join(sorted(others))} sheet. {SETTINGS_HINT}")
    return env_key, ""


# --- Planning ---------------------------------------------------------------------


def _read_lines(data: bytes, existing: Dict[str, Set[str]]) -> Tuple[List[_Line], bool]:
    lines: List[_Line] = []
    header_skipped = False
    for number, record in enumerate(_records(data), start=1):
        if not any(record):
            continue
        securable = record[0] if record else ""
        group = record[1] if len(record) > 1 else ""
        parsed, problem = parse_securable(securable)
        if number == 1 and parsed is None:
            header_skipped = True
            continue
        if len(lines) >= MAX_LINES:
            raise LedgerError(f"The file has more than {MAX_LINES} lines. Split it into smaller files.")
        line = _Line(GrantImportLine(line=number, securable=securable, group=group, status=CHANGE))
        lines.append(line)
        if parsed is None:
            line.reject(problem)
            continue
        kind, line.catalog, line.schema, line.obj = parsed
        line.out.securable_type = kind
        if any(record[2:]):
            line.reject("The line has values past column B. Use two columns: the securable and one group.")
            continue
        if not group:
            line.reject("Column B is empty. Name the group to grant SELECT to.")
            continue
        if cells.SEPARATOR in group:
            line.reject("Column B holds more than one name. Put one group on each line.")
            continue
        env_key, problem = environment_for_catalog(line.catalog, existing)
        if env_key is None:
            line.reject(problem)
            continue
        line.out.environment = env_key
    return lines, header_skipped


def _line_key(line: _Line) -> Tuple[str, ...]:
    return (line.out.environment or "", line.out.securable_type or "", cells.cell_key(line.catalog),
            cells.cell_key(line.schema), cells.cell_key(line.obj), cells.cell_key(line.out.group))


def _collapse_duplicates(lines: Sequence[_Line]) -> None:
    first: Dict[Tuple[str, ...], int] = {}
    for line in lines:
        if line.out.status != CHANGE:
            continue
        key = _line_key(line)
        if key in first:
            line.out.status = DUPLICATE
            line.out.message = f"Same as line {first[key]}, so it's written once."
        else:
            first[key] = line.out.line


def _schema_reads(rows: Sequence[RuleRow]) -> Set[Tuple[str, str, str, str, str]]:
    """``(environment, kind, catalog, schema, group)`` for every group SELECT on a catalog or schema."""
    held: Set[Tuple[str, str, str, str, str]] = set()
    for row in rows:
        if row.status != RULE_ACTIVE or cells.principal_type_key(row.principal_type) != "group":
            continue
        kind = layout.normalize_securable_type(row.securable_type)
        if kind not in ("catalog", SCHEMA):
            continue
        schema = cells.cell_key(row.schema_name) if kind == SCHEMA else ""
        for atom in cells.atoms(row):
            if cells.cell_key(atom.privilege) == "select":
                held.add((row.environment or "", kind, cells.cell_key(row.catalog), schema,
                          cells.cell_key(atom.principal)))
    return held


def _mark_covered(lines: Sequence[_Line], rows: Sequence[RuleRow]) -> None:
    held = _schema_reads(rows)
    in_file: Dict[Tuple[str, str, str, str], int] = {}
    for line in lines:
        if line.out.status == CHANGE and line.out.securable_type == SCHEMA:
            key = (line.out.environment or "", cells.cell_key(line.catalog), cells.cell_key(line.schema),
                   cells.cell_key(line.out.group))
            in_file.setdefault(key, line.out.line)
    for line in lines:
        if line.out.status != CHANGE or line.out.securable_type != VIEW:
            continue
        env, catalog = line.out.environment or "", cells.cell_key(line.catalog)
        schema, group = cells.cell_key(line.schema), cells.cell_key(line.out.group)
        schema_label = f"{line.catalog}.{line.schema}"
        if (env, SCHEMA, catalog, schema, group) in held:
            line.out.message = f"Covered by the group's SELECT on {schema_label}, already in the rules. Not written."
        elif (env, "catalog", catalog, "", group) in held:
            line.out.message = f"Covered by the group's SELECT on the catalog {line.catalog}, already in the rules. Not written."
        elif (env, catalog, schema, group) in in_file:
            line.out.message = (f"Covered by the SELECT on {schema_label} granted on line "
                                f"{in_file[(env, catalog, schema, group)]}. Not written.")
        else:
            continue
        line.out.status = COVERED


def _prerequisite_label(item: AccessItem) -> str:
    target = item.catalog if layout.normalize_securable_type(item.securable_type) == "catalog" \
        else f"{item.catalog}.{item.schema_name}"
    return f"{cells.join_cell(item.privileges)} on {target} for {item.principal_name} ({item.environment})"


def fingerprint(change: ChangeSetIn) -> str:
    payload = json.dumps([c.model_dump(mode="json") for c in change.changes], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan(db: Session, data: bytes, filename: Optional[str], *, actor: str) -> Tuple[GrantImportPreview, ChangeSetIn]:
    """The preview of a bulk grant file, and the direct edit that saving it would commit."""
    rows, versions = ledger.active_rows(db, PLANE_UC)
    lines, header_skipped = _read_lines(data, _existing_environments(rows))
    if not lines:
        raise GrantFileUnreadable("The file has no grant lines.")
    _collapse_duplicates(lines)

    candidates = [line for line in lines if line.out.status == CHANGE]
    raw_items = [AccessItem(
        action="grant", environment=line.out.environment, securable_type=line.out.securable_type or "",
        catalog=line.catalog, schema_name=line.schema, object_name=line.obj, principal_type="group",
        principal_name=line.out.group, privileges=[SELECT], plane=PLANE_UC,
    ) for line in candidates]
    for line, (item, findings) in zip(candidates, ledger.normalize_items(raw_items)):
        if findings:
            line.reject(findings[0].message)
        else:
            line.item = item
    _mark_covered(lines, rows)

    to_plan = [line for line in lines if line.out.status == CHANGE and line.item is not None]
    planned, added, diffs, outcomes = ledger.plan_with_prerequisites(PLANE_UC, rows, [l.item for l in to_plan])
    added_ids = {id(item) for item in added}
    by_index = {o.index: o for o in outcomes}
    remaining = iter(to_plan)
    for index, item in enumerate(planned):
        if id(item) in added_ids:
            continue
        line = next(remaining)
        outcome = by_index.get(index)
        if outcome is not None and outcome.status == "noop":
            line.out.status = ALREADY_HELD
        line.out.message = (outcome.reason if outcome is not None else "") or line.out.message

    change = ledger.change_set_from_diffs(diffs, versions)
    limit = int(settings.RBAC_MAX_CHANGES_PER_COMMIT or 0)
    preview = GrantImportPreview(
        filename=filename,
        header_skipped=header_skipped,
        lines=[line.out for line in lines],
        prerequisites=[_prerequisite_label(item) for item in added],
        diffs=diffs,
        max_changes=limit,
        fingerprint=fingerprint(change),
    )
    rejected = sum(1 for line in lines if line.out.status == REJECTED)
    if rejected:
        preview.blocked_reasons.append(
            f"{rejected} line{'s are' if rejected != 1 else ' is'} rejected. Fix the file and preview it again.")
    if limit > 0 and len(change.changes) > limit:
        preview.blocked_reasons.append(
            f"Saving would change {len(change.changes)} rows, and a change set can hold at most {limit}. "
            "Split the file into smaller files.")
    elif change.changes:
        checked = ledger.preview_change_set_without_reason(db, change, actor=actor)
        preview.findings = checked.findings
        preview.two_person_required = checked.two_person_required
        if checked.blocked:
            preview.blocked_reasons.append("The change breaks an access policy. See the findings.")
    preview.blocked = bool(preview.blocked_reasons)
    logger.debug("Access Rules: planned %d grant line(s) from %s into %d row change(s)",
                 len(lines), filename, len(change.changes))
    return preview, change


def planned_change(db: Session, data: bytes, filename: Optional[str], *, expected_fingerprint: str,
                   reason: str, actor: str) -> ChangeSetIn:
    """The direct edit to commit for a previewed file, with the reason.

    Raises LedgerError when the file is blocked, has nothing to save, or plans
    differently from the preview (the rules changed in between).
    """
    if not expected_fingerprint:
        raise LedgerError("Preview the file before saving it.")
    preview, change = plan(db, data, filename, actor=actor)
    if preview.fingerprint != expected_fingerprint:
        raise LedgerError("The access rules changed since the preview. Preview the file again before saving.")
    if preview.blocked:
        raise LedgerError(" ".join(preview.blocked_reasons))
    if not change.changes:
        raise LedgerError("Nothing to save: every grant in the file is already in the rules.")
    return ChangeSetIn(changes=change.changes, reason=reason)
