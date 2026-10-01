"""
Tag Plan & Diff Engine.

Queries live Unity Catalog state for objects (tables, views and their columns),
discovers current tag vocabulary across catalogs, and computes precise
before/after diffs and narrowed SQL statements (dropping redundant SETs and
non-existent UNSETs).

A *target* is a table or view, or one column of one. Targets are keyed by
``target_key``: the lower-cased three-part name, plus ``::<column>`` for a column.
"""
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.config import settings
from app.workflows.tag_sql import _escape_sql_literal

logger = logging.getLogger(__name__)

DATASET_KEY = "dataset"
CERTIFICATION_KEY = "system.certification_status"
# Keys OmniGuard owns: it sets certification when a dataset certifies and removes
# it when certification lapses. They're kept out of edits and diffs so a tag
# change can never overwrite them. Other system.* tags are ordinary tags.
RESERVED_KEYS = (CERTIFICATION_KEY,)


def is_reserved_key(key: str) -> bool:
    return str(key).strip().lower() in RESERVED_KEYS
COLUMN_SEPARATOR = "::"

# Names reach the SQL we generate, and they come from the browser, so they are
# untrusted. Table name parts are also emitted *unquoted* into GitOps migrations
# (the governance repo's contract), so anything that could end a statement or
# open a string or identifier is refused there. Column names are only ever
# emitted backtick-quoted, so spaces are fine; backticks and control characters
# are not.
_UNSAFE_NAME_PART = re.compile(r"[`'\";\s\x00-\x1f\x7f]")
_UNSAFE_COLUMN_NAME = re.compile(r"[`\x00-\x1f\x7f]")


@dataclass
class ObjectState:
    display: str
    exists: bool = False
    object_type: str = "TABLE"  # relation_type() of the table/view (also for a column target)
    tags: Dict[str, str] = field(default_factory=dict)
    all_tags: Dict[str, str] = field(default_factory=dict)  # includes reserved (OmniGuard) tags
    column: Optional[str] = None


@dataclass
class StatementPlan:
    table: str
    object_type: str
    operation: str  # "set" or "unset"
    tags: Dict[str, str] = field(default_factory=dict)
    keys: List[str] = field(default_factory=list)
    sql: str = ""
    is_noop: bool = False
    noop_reason: Optional[str] = None
    column: Optional[str] = None

    @property
    def label(self) -> str:
        return target_label(self.table, self.column)


@dataclass
class ObjectDiff:
    table: str
    object_type: str
    exists: bool
    before: Dict[str, str]
    after: Dict[str, str]
    column: Optional[str] = None
    certified: bool = False

    @property
    def label(self) -> str:
        return target_label(self.table, self.column)

    @property
    def changed_keys(self) -> List[str]:
        keys = set(self.before) | set(self.after)
        return sorted(k for k in keys if self.before.get(k) != self.after.get(k))

    @property
    def unchanged_keys(self) -> List[str]:
        keys = set(self.before) & set(self.after)
        return sorted(
            k
            for k in keys
            if self.before.get(k) == self.after.get(k)
            and not is_reserved_key(k)
        )

    @property
    def removed_keys(self) -> List[str]:
        return sorted(k for k in self.before if k not in self.after)

    @property
    def overwritten_keys(self) -> List[str]:
        return sorted(
            k
            for k in self.after
            if k in self.before and self.before[k] != self.after[k]
        )

    def tag(self, key: str, default: str = "") -> str:
        return self.before.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table": self.table,
            "column": self.column,
            "label": self.label,
            "object_type": self.object_type,
            "exists": self.exists,
            "certified": self.certified,
            "before": self.before,
            "after": self.after,
            "changed_keys": self.changed_keys,
            "removed_keys": self.removed_keys,
            "overwritten_keys": self.overwritten_keys,
            "unchanged_keys": self.unchanged_keys,
        }


@dataclass
class TagPlan:
    statement_plans: List[StatementPlan] = field(default_factory=list)
    diffs: OrderedDict[str, ObjectDiff] = field(default_factory=OrderedDict)
    missing_objects: List[str] = field(default_factory=list)

    @property
    def actionable(self) -> List[StatementPlan]:
        return [p for p in self.statement_plans if not p.is_noop]

    @property
    def noops(self) -> List[StatementPlan]:
        return [p for p in self.statement_plans if p.is_noop]

    @property
    def has_changes(self) -> bool:
        return bool(self.actionable)

    @property
    def statements(self) -> List[str]:
        return [p.sql for p in self.actionable if p.sql]

    @property
    def statement_count(self) -> int:
        return len(self.actionable)

    @property
    def object_count(self) -> int:
        return len([d for d in self.diffs.values() if d.changed_keys])

    @property
    def has_column_changes(self) -> bool:
        return any(d.column and d.changed_keys for d in self.diffs.values())

    def to_dict(self) -> Dict[str, Any]:
        changed_diffs = [d.to_dict() for d in self.diffs.values() if d.changed_keys]
        statements = [p.sql for p in self.actionable if p.sql]
        return {
            "summary": f"{self.statement_count} statement(s) to run across {self.object_count} object(s); {len(self.noops)} no-op(s).",
            "statement_count": self.statement_count,
            "object_count": self.object_count,
            "missing_objects": self.missing_objects,
            "statements": statements,
            "diffs": changed_diffs,
            "all_diffs": [d.to_dict() for d in self.diffs.values()],
        }


@dataclass
class TagVocabulary:
    values: Dict[str, Dict[str, int]] = field(default_factory=dict)
    dataset_members: Dict[str, Set[str]] = field(default_factory=dict)
    available: bool = False

    def usage(self, key: str, value: str) -> int:
        return self.values.get(key, {}).get(value, 0)

    def known_values(self, key: str) -> Dict[str, int]:
        return self.values.get(key, {})

    def is_novel(self, key: str, value: str) -> bool:
        return self.available and self.usage(key, value) == 0


# ---------------------------------------------------------------------------
# Names, keys and SQL
# ---------------------------------------------------------------------------

def _split_fqn(fqn: str) -> Tuple[str, str, str]:
    parts = (fqn or "").strip().split(".")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ValueError(f"Expected three-part name (catalog.schema.table), got: '{fqn}'")
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def _normalize_fqn(fqn: str) -> str:
    c, s, t = _split_fqn(fqn)
    return f"{c.lower()}.{s.lower()}.{t.lower()}"


def target_key(table: str, column: Optional[str] = None) -> str:
    """Stable key for a table/view, or for one of its columns."""
    norm = _normalize_fqn(table)
    return f"{norm}{COLUMN_SEPARATOR}{column.strip().lower()}" if column else norm


def target_label(table: str, column: Optional[str] = None) -> str:
    """Human-readable name for a target, e.g. ``main.sales.orders.email``."""
    return f"{table}.{column}" if column else table


def validate_target(table: str, column: Optional[str] = None) -> None:
    """Raise ``ValueError`` if a target name can't be turned into SQL safely."""
    parts = (table or "").strip().split(".")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ValueError(
            f"Invalid table identifier '{table}'. Must be a 3-part name: catalog.schema.table"
        )
    if any(_UNSAFE_NAME_PART.search(p.strip()) for p in parts):
        raise ValueError(
            f"'{table}' contains quotes, semicolons, spaces or control characters, "
            f"which can't be used in a table name here."
        )
    if column is not None:
        if not column.strip():
            raise ValueError(f"A column name is empty for '{table}'.")
        if _UNSAFE_COLUMN_NAME.search(column):
            raise ValueError(
                f"Column '{column}' on '{table}' contains a backtick or control character."
            )


def quote_identifier(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def quote_fqn(fqn: str) -> str:
    return ".".join(quote_identifier(p) for p in _split_fqn(fqn))


def _tag_pairs_sql(tags: Dict[str, str]) -> str:
    return ", ".join(f"'{_escape_sql_literal(k)}' = '{_escape_sql_literal(v)}'" for k, v in tags.items())


def _tag_keys_sql(keys: List[str]) -> str:
    return ", ".join(f"'{_escape_sql_literal(k)}'" for k in keys)


# information_schema.tables.table_type -> the relation type the plan tracks.
# Materialized views and streaming tables have their own ALTER statements
# (plain ALTER TABLE isn't accepted for them); metric views are views.
_RELATION_TYPES = {
    "VIEW": "VIEW",
    "METRIC_VIEW": "VIEW",
    "MATERIALIZED_VIEW": "MATERIALIZED_VIEW",
    "STREAMING_TABLE": "STREAMING_TABLE",
}


def relation_type(table_type: Optional[str]) -> str:
    return _RELATION_TYPES.get(str(table_type or "").upper().replace(" ", "_"), "TABLE")


def sql_object_keyword(object_type: str) -> str:
    """The ALTER keyword for a relation type: TABLE, VIEW, MATERIALIZED VIEW, STREAMING TABLE."""
    return relation_type(object_type).replace("_", " ")


def build_statement(
    object_type: str,
    table: str,
    operation: str,
    tags: Optional[Dict[str, str]] = None,
    keys: Optional[List[str]] = None,
    column: Optional[str] = None,
) -> str:
    """One SET/UNSET statement for a relation or one of its columns.

    Tables, materialized views and streaming tables tag columns with
    ``ALTER <type> … ALTER COLUMN``. View columns are the exception — ``ALTER
    VIEW`` has no ``ALTER COLUMN`` form — and go through
    ``build_view_column_statement`` one tag at a time instead.
    """
    obj = sql_object_keyword(object_type)
    target = quote_fqn(table)
    if column:
        target = f"{target} ALTER COLUMN {quote_identifier(column)}"
    if operation == "set":
        return f"ALTER {obj} {target} SET TAGS ({_tag_pairs_sql(tags or {})});"
    return f"ALTER {obj} {target} UNSET TAGS ({_tag_keys_sql(keys or [])});"


def build_view_column_statement(table: str, column: str, operation: str, key: str, value: str = "") -> str:
    """``SET TAG`` / ``UNSET TAG`` on a view column (one tag per statement).

    Tag keys and values are identifiers in this syntax, not string literals, and
    ``SET TAG`` refuses to overwrite an existing key — callers unset first.
    """
    target = f"{quote_fqn(table)}.{quote_identifier(column)}"
    if operation == "set":
        assignment = f"{quote_identifier(key)} = {quote_identifier(value)}" if value else quote_identifier(key)
        return f"SET TAG ON COLUMN {target} {assignment};"
    return f"UNSET TAG ON COLUMN {target} {quote_identifier(key)};"


def _quote_sql_literal(val: str) -> str:
    return "'" + str(val).replace("'", "''") + "'"


def _build_predicate(pairs: List[Tuple[str, str]], schema_col: str = "schema_name", table_col: str = "table_name") -> str:
    clauses = [
        f"({schema_col} = {_quote_sql_literal(s)} AND {table_col} = {_quote_sql_literal(t)})"
        for s, t in pairs
    ]
    return " OR ".join(clauses) if clauses else "1=0"


def _run_rows(provider, query: str, what: str, width: int) -> Optional[List[List[Any]]]:
    """Run a metadata query; ``None`` (logged) on failure so callers degrade.

    Rows are trimmed to ``width`` columns and shorter rows dropped, so callers can
    unpack them directly.
    """
    try:
        resp = provider.client.statement_execution.execute_statement(
            statement=query,
            warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
            wait_timeout="30s",
        )
    except Exception as e:
        logger.warning(f"Could not query {what}: {e}")
        return None
    if resp.result and resp.result.data_array:
        return [list(row)[:width] for row in resp.result.data_array if row and len(row) >= width]
    return []


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------

def fetch_live_state(
    provider,
    table_names: List[str],
    column_targets: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, ObjectState]:
    """Fetch live object types (TABLE/VIEW) and tags for tables and columns.

    ``column_targets`` are ``(table, column)`` pairs. Their tables are looked up
    too (a column's SQL depends on whether its table is a view), so the result
    also holds a state for every such table.
    """
    state: Dict[str, ObjectState] = {}
    column_targets = list(column_targets or [])
    all_tables = list(table_names) + [t for t, _ in column_targets]
    if not all_tables:
        return state

    grouped: Dict[str, List[Tuple[str, str]]] = {}
    for name in all_tables:
        norm = _normalize_fqn(name)
        if norm in state:
            continue
        c, s, t = _split_fqn(name)
        state[norm] = ObjectState(display=name, exists=False, object_type="TABLE", tags={}, all_tags={})
        grouped.setdefault(c, []).append((s, t))

    for catalog, pairs in grouped.items():
        # 1. Fetch table types
        type_predicate = _build_predicate(pairs, schema_col="table_schema", table_col="table_name")
        rows = _run_rows(
            provider,
            f"SELECT table_schema, table_name, table_type "
            f"FROM {catalog}.information_schema.tables "
            f"WHERE {type_predicate}",
            f"table types from {catalog}",
            3,
        )
        for s_name, t_name, t_type in rows or []:
            key = f"{catalog.lower()}.{s_name.lower()}.{t_name.lower()}"
            if key in state:
                state[key].exists = True
                state[key].object_type = relation_type(t_type)

        # 2. Fetch tags
        tag_predicate = _build_predicate(pairs, schema_col="schema_name", table_col="table_name")
        rows = _run_rows(
            provider,
            f"SELECT schema_name, table_name, tag_name, tag_value "
            f"FROM {catalog}.information_schema.table_tags "
            f"WHERE {tag_predicate}",
            f"table tags from {catalog}",
            4,
        )
        for s_name, t_name, tag_name, tag_val in rows or []:
            key = f"{catalog.lower()}.{s_name.lower()}.{t_name.lower()}"
            if key in state:
                val_str = "" if tag_val is None else str(tag_val)
                state[key].all_tags[tag_name] = val_str
                if not is_reserved_key(tag_name):
                    state[key].tags[tag_name] = val_str

    if column_targets:
        _fetch_column_state(provider, column_targets, state)
    return state


def _fetch_column_state(provider, column_targets: List[Tuple[str, str]], state: Dict[str, ObjectState]) -> None:
    by_catalog: Dict[str, List[Tuple[str, str, str]]] = {}
    for table, column in column_targets:
        key = target_key(table, column)
        if key in state:
            continue
        parent = state.get(_normalize_fqn(table))
        state[key] = ObjectState(
            display=target_label(table, column),
            exists=False,
            object_type=parent.object_type if parent else "TABLE",
            column=column,
        )
        c, s, t = _split_fqn(table)
        by_catalog.setdefault(c, []).append((s, t, column))

    for catalog, triples in by_catalog.items():
        def predicate(schema_col: str, table_col: str) -> str:
            return " OR ".join(
                f"({schema_col} = {_quote_sql_literal(s)} AND {table_col} = {_quote_sql_literal(t)} "
                f"AND lower(column_name) = {_quote_sql_literal(col.lower())})"
                for s, t, col in triples
            )

        rows = _run_rows(
            provider,
            f"SELECT table_schema, table_name, column_name "
            f"FROM {catalog}.information_schema.columns "
            f"WHERE {predicate('table_schema', 'table_name')}",
            f"columns from {catalog}",
            3,
        )
        for s_name, t_name, col in rows or []:
            key = f"{catalog.lower()}.{s_name.lower()}.{t_name.lower()}{COLUMN_SEPARATOR}{str(col).lower()}"
            if key in state:
                state[key].exists = True

        rows = _run_rows(
            provider,
            f"SELECT schema_name, table_name, column_name, tag_name, tag_value "
            f"FROM {catalog}.information_schema.column_tags "
            f"WHERE {predicate('schema_name', 'table_name')}",
            f"column tags from {catalog}",
            5,
        )
        for s_name, t_name, col, tag_name, tag_val in rows or []:
            key = f"{catalog.lower()}.{s_name.lower()}.{t_name.lower()}{COLUMN_SEPARATOR}{str(col).lower()}"
            if key in state:
                val_str = "" if tag_val is None else str(tag_val)
                state[key].all_tags[tag_name] = val_str
                if not is_reserved_key(tag_name):
                    state[key].tags[tag_name] = val_str


def fetch_columns(provider, table: str) -> List[Dict[str, Any]]:
    """List a table or view's columns, in order, with their editable tags."""
    c, s, t = _split_fqn(table)
    where = (
        f"table_schema = {_quote_sql_literal(s)} AND table_name = {_quote_sql_literal(t)}"
    )
    rows = _run_rows(
        provider,
        f"SELECT column_name, data_type FROM {c}.information_schema.columns "
        f"WHERE {where} ORDER BY ordinal_position",
        f"columns of {table}",
        2,
    )
    if rows is None:
        raise RuntimeError(f"Could not read the columns of {table}.")
    columns: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for name, data_type in rows:
        columns[str(name).lower()] = {"column": str(name), "data_type": data_type or "", "tags": {}}

    tag_rows = _run_rows(
        provider,
        f"SELECT column_name, tag_name, tag_value FROM {c}.information_schema.column_tags "
        f"WHERE schema_name = {_quote_sql_literal(s)} AND table_name = {_quote_sql_literal(t)}",
        f"column tags of {table}",
        3,
    )
    for name, tag_name, tag_val in tag_rows or []:
        entry = columns.get(str(name).lower())
        if entry and not is_reserved_key(tag_name):
            entry["tags"][tag_name] = "" if tag_val is None else str(tag_val)
    return list(columns.values())


def fetch_tag_vocabulary(
    provider,
    table_names: List[str],
    keys_of_interest: List[str],
    dataset_values: Optional[List[str]] = None,
    dataset_key: str = DATASET_KEY,
    include_columns: bool = False,
) -> TagVocabulary:
    """Fetch tag usage frequencies and dataset members across involved catalogs.

    With ``include_columns`` the usage counts also cover column tags, so a typo in
    a column classification is caught against the values other columns use.
    """
    vocabulary = TagVocabulary()
    if not table_names or not keys_of_interest:
        return vocabulary

    catalogs = sorted({_split_fqn(name)[0] for name in table_names})
    key_list_sql = ", ".join(_quote_sql_literal(k) for k in keys_of_interest)
    sources = ["table_tags"] + (["column_tags"] if include_columns else [])

    for catalog in catalogs:
        # Aggregated tag value usage
        for source in sources:
            rows = _run_rows(
                provider,
                f"SELECT tag_name, tag_value, count(*) AS n "
                f"FROM {catalog}.information_schema.{source} "
                f"WHERE tag_name IN ({key_list_sql}) "
                f"GROUP BY tag_name, tag_value",
                f"tag vocabulary ({source}) for {catalog}",
                3,
            )
            if rows is None:
                continue
            if rows:
                vocabulary.available = True
            for tag_name, tag_val, count in rows:
                bucket = vocabulary.values.setdefault(str(tag_name), {})
                val_str = "" if tag_val is None else str(tag_val)
                try:
                    uses = int(count or 0)
                except (TypeError, ValueError):
                    continue
                bucket[val_str] = bucket.get(val_str, 0) + uses

        # Dataset membership
        if dataset_values:
            val_list_sql = ", ".join(_quote_sql_literal(v) for v in dataset_values if v)
            if val_list_sql:
                rows = _run_rows(
                    provider,
                    f"SELECT catalog_name, schema_name, table_name, tag_value "
                    f"FROM {catalog}.information_schema.table_tags "
                    f"WHERE tag_name IN ({_quote_sql_literal(dataset_key)}, 'data_set') "
                    f"AND tag_value IN ({val_list_sql})",
                    f"dataset membership for {catalog}",
                    4,
                )
                for c_name, s_name, t_name, ds_val in rows or []:
                    if ds_val:
                        members = vocabulary.dataset_members.setdefault(str(ds_val), set())
                        members.add(f"{str(c_name).lower()}.{str(s_name).lower()}.{str(t_name).lower()}")

    return vocabulary


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _view_column_statements(
    table: str, column: str, current: Dict[str, str], set_tags: Dict[str, str], unset_keys: List[str]
) -> List[StatementPlan]:
    # SET TAG can't overwrite, so a changed value is an UNSET followed by a SET.
    plans: List[StatementPlan] = []
    for key in unset_keys + [k for k in set_tags if k in current]:
        plans.append(StatementPlan(
            table=table, column=column, object_type="VIEW", operation="unset", keys=[key],
            sql=build_view_column_statement(table, column, "unset", key),
        ))
    for key, value in set_tags.items():
        plans.append(StatementPlan(
            table=table, column=column, object_type="VIEW", operation="set", tags={key: value},
            sql=build_view_column_statement(table, column, "set", key, value),
        ))
    return plans


def build_tag_plan(
    tables_payload: List[Dict[str, Any]],
    live_state: Dict[str, ObjectState],
) -> TagPlan:
    """Build a deterministic TagPlan from requested desired tags and live state.

    Each item is ``{"table": fqn, "desired_tags": {...}}`` plus an optional
    ``"column"`` to target one of the table's columns instead of the table.
    """
    plan = TagPlan()

    for item in tables_payload:
        full_name = item.get("table", "")
        if not full_name:
            continue
        column = item.get("column") or None
        key = target_key(full_name, column)
        label = target_label(full_name, column)
        parent = live_state.get(_normalize_fqn(full_name))
        state = live_state.get(key)
        if not state:
            state = ObjectState(
                display=label,
                exists=False,
                object_type=parent.object_type if (column and parent) else "TABLE",
                tags={},
                all_tags={},
                column=column,
            )

        if not state.exists:
            if label not in plan.missing_objects:
                plan.missing_objects.append(label)

        desired_raw = item.get("desired_tags") or {}
        desired = {
            str(k): str(v)
            for k, v in desired_raw.items()
            if k and not is_reserved_key(k)
        }
        # Dataset partition tags ('dataset', 'data_set') are structural grouping metadata;
        # if present in live state and omitted in a partial edit, preserve them so tables are not orphaned.
        if not column:
            for ds_key in ("dataset", "data_set"):
                if ds_key in state.tags and ds_key not in desired:
                    desired[ds_key] = state.tags[ds_key]

        current = dict(state.tags)
        cert_source = parent if column else state
        certified = bool(
            cert_source
            and (cert_source.all_tags.get(CERTIFICATION_KEY) or "").lower() == "certified"
        )

        # Diff calculation
        diff = ObjectDiff(
            table=full_name,
            object_type=state.object_type,
            exists=state.exists,
            before=current,
            after=desired,
            column=column,
            certified=certified,
        )
        plan.diffs[key] = diff

        # Statements calculation
        obj_type = sql_object_keyword(state.object_type)
        set_tags = {k: v for k, v in desired.items() if current.get(k) != v}
        unset_keys = [k for k in current if k not in desired]

        if column and obj_type == "VIEW":
            plan.statement_plans.extend(
                _view_column_statements(full_name, column, current, set_tags, unset_keys)
            )
            if not set_tags and not unset_keys and desired:
                plan.statement_plans.append(StatementPlan(
                    table=full_name, column=column, object_type=obj_type, operation="set",
                    is_noop=True, noop_reason="values already match",
                ))
            continue

        if set_tags:
            plan.statement_plans.append(
                StatementPlan(
                    table=full_name,
                    column=column,
                    object_type=obj_type,
                    operation="set",
                    tags=set_tags,
                    sql=build_statement(obj_type, full_name, "set", tags=set_tags, column=column),
                    is_noop=False,
                )
            )
        elif not unset_keys and desired:
            plan.statement_plans.append(
                StatementPlan(
                    table=full_name,
                    column=column,
                    object_type=obj_type,
                    operation="set",
                    tags={},
                    sql="",
                    is_noop=True,
                    noop_reason="values already match",
                )
            )

        if unset_keys:
            plan.statement_plans.append(
                StatementPlan(
                    table=full_name,
                    column=column,
                    object_type=obj_type,
                    operation="unset",
                    keys=unset_keys,
                    sql=build_statement(obj_type, full_name, "unset", keys=unset_keys, column=column),
                    is_noop=False,
                )
            )

    return plan
