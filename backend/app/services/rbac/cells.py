"""Atom semantics of packed workbook cells, and the deterministic row-edit planner.

Pure functions over ``RuleRow`` snapshots; no database access. See ADR 0005 D1
for the edit convention this module implements.

Principals and privileges compare case-insensitively and trimmed. Text already
in a row keeps its spelling; values a plan adds take the item's spelling.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from app.db.rbac import PLANE_UC, PLANE_WORKSPACE
from app.models.rbac import CANONICAL_FIELDS, AccessItem, ItemOutcome, RowDiff, RuleRow
from app.services.rbac import layout

SEPARATOR = ";"

USE_CATALOG = "USE_CATALOG"
USE_SCHEMA = "USE_SCHEMA"
# Privileges that only exist to reach a child securable. A revoke that leaves
# a principal nothing but these drops the principal from the row entirely.
PREREQUISITE_PRIVILEGES = frozenset({USE_CATALOG, USE_SCHEMA})
_PREREQUISITE_KEYS = frozenset(p.lower() for p in PREREQUISITE_PRIVILEGES)

# UC securables that live inside a schema (they need USE_SCHEMA and USE_CATALOG).
SCHEMA_OBJECT_TYPES = frozenset({
    "table", "view", "materialized view", "streaming table",
    "volume", "function", "model", "registered model",
})


@dataclass(frozen=True)
class Atom:
    """One principal x one privilege on one row's securable."""

    principal: str
    privilege: str


def split_cell(value: Optional[str]) -> List[str]:
    """``"a; b;;c"`` -> ``["a", "b", "c"]`` (trimmed, empties dropped, order kept)."""
    return [part.strip() for part in (value or "").split(SEPARATOR) if part.strip()]


def join_cell(values: Sequence[str]) -> str:
    """``["a", "b"]`` -> ``"a;b"`` (the workbook's spelling, no spaces)."""
    return SEPARATOR.join(v.strip() for v in values if v and v.strip())


def atoms(row: RuleRow) -> List[Atom]:
    """The cartesian product of a row's principals and privileges."""
    privileges = split_cell(row.privilege)
    return [Atom(p, q) for p in split_cell(row.principal_name) for q in privileges]


def cell_key(value: Optional[str]) -> str:
    """Comparison key for a principal, privilege or securable cell: trimmed, lowercase."""
    return (value or "").strip().lower()


def principal_type_key(value: Optional[str]) -> str:
    """``group``, ``service principal`` or ``user`` for any common spelling, else the cleaned value."""
    cleaned = re.sub(r"[\s_\-]+", " ", (value or "").strip().lower())
    compact = cleaned.replace(" ", "")
    if compact in {"group", "groups"}:
        return "group"
    if compact in {"serviceprincipal", "serviceprincipals", "sp", "spn"}:
        return "service principal"
    if compact in {"user", "users"}:
        return "user"
    return cleaned


def securable_key(obj: Any, plane: str) -> Tuple[str, ...]:
    """The securable identity of a row or item on ``plane`` (layout.SECURABLE_FIELDS), normalized."""
    out = []
    for field in layout.SECURABLE_FIELDS.get(plane, ()):
        value = getattr(obj, field, "") or ""
        out.append(layout.normalize_securable_type(value) if field == "securable_type" else cell_key(value))
    return tuple(out)


def describe_securable(obj: Any, plane: str) -> str:
    """``schema main.sales``, ``folder /Shared in ws-dev``, ``workspace ws-dev``."""
    kind = (obj.securable_type or "").strip()
    if plane == PLANE_WORKSPACE:
        name = (obj.object_name or "").strip()
        workspace = (obj.workspace_name or "").strip()
        target = f"{name} in {workspace}" if name and workspace else (name or workspace)
    else:
        parts = [
            (getattr(obj, f, "") or "").strip()
            for f in layout.SECURABLE_FIELDS.get(plane, ())
            if f not in ("securable_type", "workspace_name")
        ]
        target = ".".join(p for p in parts if p)
    return f"{kind} {target}".strip()


def item_plane(item: AccessItem) -> Optional[str]:
    return layout.plane_for_securable_type(item.securable_type) or item.plane


def row_snapshot(row: RuleRow, *, existing: bool = True) -> Dict[str, Any]:
    """A RowDiff before / after snapshot: canonical cells + extra (+ position / version on existing rows)."""
    snap: Dict[str, Any] = {f: getattr(row, f) for f in CANONICAL_FIELDS}
    snap["extra"] = dict(row.extra)
    if existing:
        snap["position"] = row.position
        snap["version"] = row.version
    return snap


def _dedupe(values: Sequence[str]) -> List[str]:
    """Flatten packed values, trim, and drop case-insensitive repeats (first spelling wins)."""
    seen: Set[str] = set()
    out: List[str] = []
    for value in values:
        for part in split_cell(value):
            if cell_key(part) not in seen:
                seen.add(cell_key(part))
                out.append(part)
    return out


def _keys(cell: Optional[str]) -> Set[str]:
    return {cell_key(v) for v in split_cell(cell)}


def _append_to_cell(cell: Optional[str], value: str) -> str:
    """Append to the end of a packed cell, leaving the existing text exactly as written."""
    base = (cell or "").rstrip().rstrip(SEPARATOR).rstrip()
    return f"{base}{SEPARATOR}{value}" if base else value


def _remove_from_cell(cell: Optional[str], value: str) -> str:
    """Drop every occurrence of ``value`` from a packed cell, leaving the other parts as written."""
    target = cell_key(value)
    parts = [p for p in (cell or "").split(SEPARATOR) if cell_key(p) != target or not p.strip()]
    return SEPARATOR.join(parts).strip().strip(SEPARATOR).strip()


def _with_leading(item: AccessItem, privilege: str) -> AccessItem:
    if any(cell_key(p) == privilege.lower() for p in _dedupe(item.privileges)):
        return item
    return item.model_copy(update={"privileges": [privilege, *item.privileges]})


def _environment_key(value: Optional[str]) -> str:
    return cell_key(layout.resolve_environment(value) or value)


# --- Prerequisites ------------------------------------------------------------


def add_prerequisites(items: Sequence[AccessItem], rows: Sequence[RuleRow]) -> Tuple[List[AccessItem], List[AccessItem]]:
    """Return ``(items_with_prerequisites, added)``.

    Grants carry their prerequisite in the same privilege set, as the sheet does
    (``SELECT`` on a schema -> ``USE_SCHEMA;SELECT``; a catalog grant gets
    ``USE_CATALOG``). An object inside a schema (table, view, volume, ...) gets
    nothing extra in its own row, but a ``USE_SCHEMA`` grant on the parent schema
    is added as its own item unless an active row or another grant in ``items``
    already gives the principal ``USE_SCHEMA`` on that schema (or on its
    catalog, which inherits). Likewise ``USE_CATALOG`` on the catalog for
    anything below catalog level. Added items sit just before the first item
    that needs them, ``USE_CATALOG`` first, and are never duplicated. Only the
    UC plane is affected; revokes are untouched. Rows and items match on
    environment, principal type and principal (case-insensitive).
    """
    normalized: List[AccessItem] = []
    for item in items:
        if item.action == "grant" and item_plane(item) == PLANE_UC:
            kind = layout.normalize_securable_type(item.securable_type)
            if kind == "schema":
                item = _with_leading(item, USE_SCHEMA)
            elif kind == "catalog":
                item = _with_leading(item, USE_CATALOG)
        normalized.append(item)

    # (env, principal type, principal, securable type, catalog, schema, privilege)
    held: Set[Tuple[str, ...]] = set()

    def _record(env: str, ptype: str, principal: str, kind: str, catalog: str, schema: str, privilege: str) -> None:
        schema_key = cell_key(schema) if kind == "schema" else ""
        held.add((env, ptype, cell_key(principal), kind, cell_key(catalog), schema_key, cell_key(privilege)))

    for row in rows:
        if row.status != "active" or row.plane != PLANE_UC:
            continue
        kind = layout.normalize_securable_type(row.securable_type)
        if kind not in ("catalog", "schema"):
            continue
        env, ptype = cell_key(row.environment), principal_type_key(row.principal_type)
        for atom in atoms(row):
            _record(env, ptype, atom.principal, kind, row.catalog, row.schema_name, atom.privilege)
    for item in normalized:
        if item.action != "grant" or item_plane(item) != PLANE_UC:
            continue
        kind = layout.normalize_securable_type(item.securable_type)
        if kind not in ("catalog", "schema"):
            continue
        env, ptype = _environment_key(item.environment), principal_type_key(item.principal_type)
        for privilege in _dedupe(item.privileges):
            _record(env, ptype, item.principal_name, kind, item.catalog, item.schema_name, privilege)

    def _holds(item: AccessItem, kind: str, privilege: str) -> bool:
        env, ptype = _environment_key(item.environment), principal_type_key(item.principal_type)
        base = (env, ptype, cell_key(item.principal_name))
        catalog_level = (*base, "catalog", cell_key(item.catalog), "", privilege.lower())
        if kind == "catalog":
            return catalog_level in held
        return catalog_level in held or (*base, "schema", cell_key(item.catalog), cell_key(item.schema_name), privilege.lower()) in held

    out: List[AccessItem] = []
    added: List[AccessItem] = []
    for item in normalized:
        if item.action == "grant" and item_plane(item) == PLANE_UC and item.catalog.strip():
            kind = layout.normalize_securable_type(item.securable_type)
            needed: List[Tuple[str, str]] = []
            if kind == "schema" or kind in SCHEMA_OBJECT_TYPES:
                needed.append(("catalog", USE_CATALOG))
            if kind in SCHEMA_OBJECT_TYPES and item.schema_name.strip():
                needed.append(("schema", USE_SCHEMA))
            for prereq_kind, privilege in needed:
                if _holds(item, prereq_kind, privilege):
                    continue
                prereq = AccessItem(
                    action="grant",
                    environment=item.environment,
                    securable_type=prereq_kind,
                    catalog=item.catalog,
                    schema_name=item.schema_name if prereq_kind == "schema" else "",
                    principal_type=item.principal_type,
                    principal_name=item.principal_name,
                    privileges=[privilege],
                    plane=PLANE_UC,
                )
                _record(_environment_key(item.environment), principal_type_key(item.principal_type),
                        item.principal_name, prereq_kind, item.catalog, prereq.schema_name, privilege)
                out.append(prereq)
                added.append(prereq)
        out.append(item)
    return out, added


# --- Row-edit planner ---------------------------------------------------------


@dataclass
class _Slot:
    row: RuleRow  # working copy
    original: Optional[RuleRow]  # None for a row the plan creates


class _Planner:
    def __init__(
        self,
        rows: Sequence[RuleRow],
        owner_for_item: Callable[[AccessItem], str],
        environment_value_for_item: Callable[[AccessItem], str],
    ):
        ordered = sorted(
            ((i, r) for i, r in enumerate(rows) if r.status == "active"),
            key=lambda pair: (pair[1].position, pair[0]),
        )
        self.slots: List[_Slot] = [_Slot(r.model_copy(deep=True), r) for _, r in ordered]
        self.touched: List[int] = []
        self.owner_for_item = owner_for_item
        self.environment_value_for_item = environment_value_for_item

    def touch(self, index: int) -> None:
        if index not in self.touched:
            self.touched.append(index)

    def on_securable(self, sheet: str, plane: str, item: AccessItem) -> List[int]:
        """Slot indices on ``sheet`` for the item's securable and principal type, in sheet order."""
        sec = securable_key(item, plane)
        ptype = principal_type_key(item.principal_type)
        sheet_key = cell_key(sheet)
        return [
            i for i, slot in enumerate(self.slots)
            if cell_key(slot.row.sheet) == sheet_key
            and securable_key(slot.row, plane) == sec
            and principal_type_key(slot.row.principal_type) == ptype
        ]

    def held_by(self, candidates: List[int], principal: str) -> Set[str]:
        out: Set[str] = set()
        for i in candidates:
            if cell_key(principal) in _keys(self.slots[i].row.principal_name):
                out |= _keys(self.slots[i].row.privilege)
        return out

    def grant(
        self, item: AccessItem, sheet: str, plane: str, principal: str, privileges: List[str],
        *, owner: Optional[str] = None,
    ) -> Tuple[str, str]:
        label = describe_securable(item, plane)
        candidates = self.on_securable(sheet, plane, item)
        wanted = {cell_key(p) for p in privileges}
        if wanted <= self.held_by(candidates, principal):
            return "noop", f"{principal} already holds {join_cell(privileges)} on {label}"
        has_owner = "owner" in layout.plane_fields(plane)
        if has_owner:
            owner = (owner if owner is not None else self.owner_for_item(item)) or ""
        else:
            owner = ""
        for i in candidates:
            row = self.slots[i].row
            if has_owner and cell_key(row.owner) != cell_key(owner):
                continue
            if _keys(row.privilege) != wanted:
                continue
            row.principal_name = _append_to_cell(row.principal_name, principal)
            self.touch(i)
            return "change", f"adds {principal} to the existing {row.privilege.strip()} row on {label}"
        self.slots.append(_Slot(self._new_row(item, sheet, plane, principal, privileges, owner), None))
        self.touch(len(self.slots) - 1)
        return "change", f"adds a new {join_cell(privileges)} row for {principal} on {label}"

    def revoke(self, item: AccessItem, sheet: str, plane: str, principal: str, privileges: List[str]) -> Tuple[str, str]:
        label = describe_securable(item, plane)
        revoked = {cell_key(p) for p in privileges}
        principal_key = cell_key(principal)
        targets = [
            i for i in self.on_securable(sheet, plane, item)
            if principal_key in _keys(self.slots[i].row.principal_name)
            and _keys(self.slots[i].row.privilege) & revoked
        ]
        if not targets:
            return "noop", f"{principal} doesn't hold {join_cell(privileges)} on {label}"
        remainders: List[Tuple[List[str], str]] = []
        for i in targets:
            row = self.slots[i].row
            row.principal_name = _remove_from_cell(row.principal_name, principal)
            self.touch(i)
            remaining = [p for p in split_cell(row.privilege) if cell_key(p) not in revoked]
            if any(cell_key(p) not in _PREREQUISITE_KEYS for p in remaining):
                remainders.append((_dedupe(remaining), row.owner))
        for remaining, owner in remainders:
            self.grant(item, sheet, plane, principal, remaining, owner=owner)
        kept = _dedupe([p for remaining, _ in remainders for p in remaining])
        rows_text = "1 row" if len(targets) == 1 else f"{len(targets)} rows"
        reason = f"removes {principal} from {rows_text} on {label}"
        if kept:
            reason += f" and keeps {join_cell(kept)}"
        return "change", reason

    def _new_row(
        self, item: AccessItem, sheet: str, plane: str, principal: str, privileges: List[str], owner: str,
    ) -> RuleRow:
        cells: Dict[str, str] = {f: "" for f in CANONICAL_FIELDS}
        cells["environment_value"] = (self.environment_value_for_item(item) or "").strip()
        for field in layout.SECURABLE_FIELDS.get(plane, ()):
            cells[field] = (getattr(item, field, "") or "").strip()
        cells["securable_type"] = layout.normalize_securable_type(item.securable_type)
        cells["owner"] = owner
        ptype = principal_type_key(item.principal_type)
        cells["principal_type"] = ptype if ptype in ("group", "service principal", "user") else item.principal_type.strip()
        cells["principal_name"] = principal
        cells["privilege"] = join_cell(privileges)
        return RuleRow(sheet=sheet, plane=plane, environment=self._environment_for_new_row(item, sheet), **cells)

    def _environment_for_new_row(self, item: AccessItem, sheet: str) -> Optional[str]:
        spec = layout.sheet_spec(sheet)
        if spec is not None and spec.environment:
            return spec.environment
        resolved = layout.resolve_environment(item.environment)
        if resolved:
            return resolved
        if spec is None:
            for slot in self.slots:
                if slot.original is not None and cell_key(slot.row.sheet) == cell_key(sheet) and slot.row.environment:
                    return slot.row.environment
        return None

    def diffs(self) -> List[RowDiff]:
        out: List[RowDiff] = []
        for i in self.touched:
            slot = self.slots[i]
            row = slot.row
            emptied = not split_cell(row.principal_name) or not split_cell(row.privilege)
            if slot.original is None:
                if emptied:
                    continue
                out.append(RowDiff(op="create", rule_id=None, sheet=row.sheet, plane=row.plane,
                                   environment=row.environment, before=None, after=row_snapshot(row, existing=False)))
                continue
            before = row_snapshot(slot.original)
            if emptied:
                out.append(RowDiff(op="delete", rule_id=slot.original.id, sheet=row.sheet, plane=row.plane,
                                   environment=row.environment, before=before, after=None))
                continue
            after = row_snapshot(row)
            if after == before:
                continue
            out.append(RowDiff(op="update", rule_id=slot.original.id, sheet=row.sheet, plane=row.plane,
                               environment=row.environment, before=before, after=after))
        return out


def plan_edits(
    rows: Sequence[RuleRow],
    items: Sequence[AccessItem],
    *,
    sheet_for_item: Callable[[AccessItem], Optional[str]],
    owner_for_item: Callable[[AccessItem], str],
    environment_value_for_item: Callable[[AccessItem], str],
) -> Tuple[List[RowDiff], List[ItemOutcome]]:
    """Turn grant / revoke items into workbook-style row edits.

    ``rows`` are the active rows of the affected sheets. Rules (ADR 0005 D1):

    - Grant: if the principal already holds every requested privilege on the
      securable (across any rows of the sheet with its principal type), noop.
      Otherwise find an active row on the same sheet with the same securable,
      owner (UC / metastore only; ``owner_for_item``) and principal type and
      exactly the requested privilege set; append the principal to the end of
      its principal list. Otherwise append a new row.
    - Revoke: remove the principal from every row on the securable whose set
      intersects the revoked privileges. If what's left of a row's set is more
      than prerequisites (USE_CATALOG / USE_SCHEMA), grant it again with the
      grant rule, under that row's owner. Nothing to remove -> noop.
    - A row left with no principals or no privileges is deleted. A row the plan
      created and then emptied, or a row edited back to how it was, has no diff.

    Edits to the same row from several items merge into one diff (``before`` is
    the original row, ``after`` the final state); a row an earlier item created
    takes later appends. Diffs are in first-touch order, outcomes in item order
    (``index`` = position in ``items``); outcome status is ``change`` or ``noop``,
    never ``blocked`` (policy decides that).

    ``RowDiff.environment`` is the row's ``environment`` for existing rows. For
    a new row it's the sheet's environment (``layout.sheet_spec``), else the
    item's resolved environment (the metastore sheet), else that of another row
    on the same unmanaged sheet, else None.

    Raises ValueError when ``sheet_for_item`` returns no sheet, or the item's
    plane can't be told; callers validate items first.
    """
    planner = _Planner(rows, owner_for_item, environment_value_for_item)
    outcomes: List[ItemOutcome] = []
    for index, item in enumerate(items):
        sheet = sheet_for_item(item)
        if not sheet:
            raise ValueError(f"No sheet for item {index} ({item.securable_type!r} in {item.environment!r}).")
        spec = layout.sheet_spec(sheet)
        plane = item_plane(item) or (spec.plane if spec else None)
        if plane not in layout.SECURABLE_FIELDS:
            raise ValueError(f"Unknown plane for item {index} ({item.securable_type!r}).")
        principal = (item.principal_name or "").strip()
        privileges = _dedupe(item.privileges)
        if not principal:
            outcomes.append(ItemOutcome(index=index, item=item, status="noop", reason="no principal given"))
            continue
        if not privileges:
            outcomes.append(ItemOutcome(index=index, item=item, status="noop", reason="no privileges given"))
            continue
        if item.action == "revoke":
            status, reason = planner.revoke(item, sheet, plane, principal, privileges)
        else:
            status, reason = planner.grant(item, sheet, plane, principal, privileges)
        outcomes.append(ItemOutcome(index=index, item=item, status=status, reason=reason))
    return planner.diffs(), outcomes
