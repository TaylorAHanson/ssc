"""Desired vs observed for one object, under full enforcement (ADR-0006 D3). Workstream C.

Tag keys are compared exactly as stored; only exemption is case-insensitive.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.services.metadata.sql import VIEW_TYPES, build_comment_statement, build_tag_statement
from app.services.metadata.types import (
    COLUMN_SEPARATOR,
    RELATION_TYPES,
    DesiredState,
    DriftEntry,
    ObjectDiff,
    ObjectKey,
    ObservedState,
    PlanItem,
    normalize_tags,
    normalize_value,
)

# information_schema.tables.table_type values that need their own statement form.
# Anything else (MANAGED, EXTERNAL, FOREIGN, shallow clones, ...) is a table.
_TABLE_TYPES = {
    "VIEW": "view",
    "METRIC_VIEW": "metric_view",
    "MATERIALIZED_VIEW": "materialized_view",
    "STREAMING_TABLE": "streaming_table",
}
# Execution order within one object. Unsets run before sets, so a key whose case
# changed is removed before its replacement is written, and a view column's changed
# value can be unset before SET TAG (which can't overwrite) writes it again.
_OPERATION_ORDER = {"unset": 0, "set": 1, "comment": 2}


def exempt_set(exempt_keys: Iterable[str]) -> Set[str]:
    """Lowercased exempt keys."""
    return {str(k).strip().lower() for k in (exempt_keys or ()) if str(k).strip()}


def _without(tags: Dict[str, Optional[str]], exempt: Set[str]) -> Dict[str, Optional[str]]:
    return {k: v for k, v in tags.items() if k.strip().lower() not in exempt}


def _desired_tags(desired: DesiredState) -> Dict[str, Optional[str]]:
    tags = desired.tags or {t.key: t.value for t in desired.tag_details}
    return normalize_tags(tags)


def _observed_tags(observed: Optional[ObservedState]) -> Dict[str, Optional[str]]:
    if observed is None or not observed.present:
        return {}
    return normalize_tags(observed.tags)


def _observed_comment(observed: Optional[ObservedState]) -> Optional[str]:
    if observed is None or not observed.present:
        return None
    return normalize_value(observed.comment)


def diff(desired: DesiredState, observed: Optional[ObservedState], *, exempt_keys: Iterable[str]) -> ObjectDiff:
    """Tags to set (missing or different), tags to unset (in UC, not desired) and the comment change.

    Exempt keys are ignored on both sides. With no observed row, or one that's no longer
    present, everything desired is to be set (``observed_present=False``); whether to
    apply that is the reconciler's call.
    """
    exempt = exempt_set(exempt_keys)
    want = _without(_desired_tags(desired), exempt)
    have = _without(_observed_tags(observed), exempt)
    before = _observed_comment(observed)
    after = normalize_value(desired.comment.value)
    return ObjectDiff(
        fqn_key=desired.fqn_key,
        name=desired.display_name or desired.key.display,
        securable_type=desired.securable_type,
        observed_present=observed is not None and observed.present,
        to_set={k: v for k, v in sorted(want.items()) if k not in have or have[k] != v},
        to_unset=sorted(k for k in have if k not in want),
        comment_changed=before != after,
        comment_before=before,
        comment_after=after,
    )


def drift_entries(
    desired: Optional[DesiredState], observed: Optional[ObservedState], *, exempt_keys: Iterable[str],
) -> List[DriftEntry]:
    """The drawer's per-key comparison; exempt keys are listed with ``exempt=True``.

    Tags first (sorted by key), then the comment. Values equal on both sides aren't
    listed. An unmanaged object (no desired row) has no drift. Count only entries with
    ``exempt=False`` as drift.
    """
    if desired is None:
        return []
    exempt = exempt_set(exempt_keys)
    want = _desired_tags(desired)
    have = _observed_tags(observed)
    entries: List[DriftEntry] = []
    for key in sorted(set(want) | set(have)):
        in_want, in_have = key in want, key in have
        if in_want and in_have and want[key] == have[key]:
            continue
        kind = "changed" if in_want and in_have else ("missing" if in_want else "extra")
        entries.append(DriftEntry(
            field="tag", key=key, desired=want.get(key), observed=have.get(key), kind=kind,
            exempt=key.strip().lower() in exempt,
        ))
    want_comment = normalize_value(desired.comment.value)
    have_comment = _observed_comment(observed)
    if want_comment != have_comment:
        kind = "changed" if want_comment is not None and have_comment is not None else (
            "missing" if want_comment is not None else "extra")
        entries.append(DriftEntry(field="comment", desired=want_comment, observed=have_comment, kind=kind))
    return entries


def securable_type_from_table_type(table_type: Optional[str]) -> Optional[str]:
    """``information_schema`` table_type -> relation securable type; None when unknown."""
    text = str(table_type or "").strip().upper().replace(" ", "_")
    if not text:
        return None
    return _TABLE_TYPES.get(text, "table")


def _relation_type_of(*rows: Optional[ObservedState]) -> Optional[str]:
    for row in rows:
        if row is None:
            continue
        found = securable_type_from_table_type(row.table_type)
        if found:
            return found
        if row.securable_type in RELATION_TYPES:
            return row.securable_type
    return None


def statement_type(
    object_diff: ObjectDiff, observed: Optional[ObservedState], *, parent: Optional[ObservedState] = None,
) -> str:
    """The securable type whose statement form applies: UC's type wins over the desired row's.

    For a column this is its relation's type, read from ``parent`` (the relation's
    observed row), else the column row's own ``table_type``, else ``table``.
    """
    key = _object_key(object_diff, observed)
    if key.level == "catalog":
        return "catalog"
    if key.level == "schema":
        return "schema"
    if key.level == "column":
        return _relation_type_of(parent, observed) or "table"
    desired_type = object_diff.securable_type if object_diff.securable_type in RELATION_TYPES else None
    return _relation_type_of(observed) or desired_type or "table"


def _object_key(object_diff: ObjectDiff, observed: Optional[ObservedState]) -> ObjectKey:
    """UC's spelling of the name when observed, else the normalized key."""
    if observed is not None and observed.key.fqn_key == object_diff.fqn_key:
        return observed.key
    return ObjectKey.from_fqn_key(object_diff.fqn_key)


def plan_sort_key(item: PlanItem) -> Tuple:
    """Relation, then column, then unset / set / comment, then keys: stable across runs.

    Grouping by relation keeps a table's statements next to its columns'; within one
    object it fixes the order statements must run in (see ``_OPERATION_ORDER``).
    """
    relation, _, column = item.fqn_key.partition(COLUMN_SEPARATOR)
    keys = tuple(item.keys) if item.keys else tuple(item.tags)
    return relation, column, _OPERATION_ORDER.get(item.operation, len(_OPERATION_ORDER)), keys, item.sql


def plan(
    object_diff: ObjectDiff,
    *,
    observed: Optional[ObservedState],
    object_id: Optional[str] = None,
    parent: Optional[ObservedState] = None,
) -> List[PlanItem]:
    """Statements that bring UC to the desired state, typed from the observed table type.

    One item per statement: an unset (``removals`` = keys removed), a set and a comment
    (``removals=1`` when it clears a description) for most objects. A view column takes one statement per tag, and a changed value
    there is an unset (``removals=0``, it isn't a removal) followed by a set, since
    ``SET TAG`` can't overwrite. ``parent`` is the relation's observed row, used to
    type a column's statements. Sorted with ``plan_sort_key``.
    """
    key = _object_key(object_diff, observed)
    column = key.column_name
    stype = statement_type(object_diff, observed, parent=parent)
    base = {
        "fqn_key": object_diff.fqn_key,
        "object_id": object_id,
        "name": key.name,
        "column": column,
        "securable_type": "column" if column else stype,
    }
    to_set = dict(sorted(object_diff.to_set.items()))
    to_unset = sorted(object_diff.to_unset)
    items: List[PlanItem] = []

    def tag_sql(operation: str, **kwargs) -> List[str]:
        return build_tag_statement(stype, key.name, operation, column=column, **kwargs)

    if column is not None and stype in VIEW_TYPES:
        have = _observed_tags(observed)
        for k in to_unset:
            items.append(PlanItem(**base, operation="unset", keys=[k], sql=tag_sql("unset", keys=[k])[0], removals=1))
        for k in to_set:
            if k in have:
                items.append(PlanItem(**base, operation="unset", keys=[k], sql=tag_sql("unset", keys=[k])[0]))
        for k, v in to_set.items():
            items.append(PlanItem(**base, operation="set", tags={k: v}, sql=tag_sql("set", tags={k: v})[0]))
    else:
        if to_unset:
            items.append(PlanItem(
                **base, operation="unset", keys=to_unset, sql=tag_sql("unset", keys=to_unset)[0],
                removals=len(to_unset),
            ))
        if to_set:
            items.append(PlanItem(**base, operation="set", tags=to_set, sql=tag_sql("set", tags=to_set)[0]))
    if object_diff.comment_changed:
        cleared = object_diff.comment_after is None and object_diff.comment_before is not None
        items.append(PlanItem(
            **base, operation="comment", comment=object_diff.comment_after,
            sql=build_comment_statement(stype, key.name, object_diff.comment_after, column=column),
            removals=1 if cleared else 0,
        ))
    return sorted(items, key=plan_sort_key)
