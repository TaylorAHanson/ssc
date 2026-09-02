"""
Tag Plan & Diff Engine.

Queries live Unity Catalog state for objects (tables and views), discovers
current tag vocabulary across catalogs, and computes precise before/after diffs
and narrowed SQL statements (dropping redundant SETs and non-existent UNSETs).
"""
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.config import settings
from app.workflows.tag_sql import _escape_sql_literal

logger = logging.getLogger(__name__)

HIDDEN_DISPLAY_PREFIXES = ("system.",)
DATASET_KEY = "dataset"


@dataclass
class ObjectState:
    display: str
    exists: bool = False
    object_type: str = "TABLE"
    tags: Dict[str, str] = field(default_factory=dict)
    all_tags: Dict[str, str] = field(default_factory=dict)  # includes system.* tags


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


@dataclass
class ObjectDiff:
    table: str
    object_type: str
    exists: bool
    before: Dict[str, str]
    after: Dict[str, str]

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
            and not k.lower().startswith(HIDDEN_DISPLAY_PREFIXES)
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
            "object_type": self.object_type,
            "exists": self.exists,
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


def _split_fqn(fqn: str) -> Tuple[str, str, str]:
    parts = (fqn or "").strip().split(".")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ValueError(f"Expected three-part name (catalog.schema.table), got: '{fqn}'")
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def _normalize_fqn(fqn: str) -> str:
    c, s, t = _split_fqn(fqn)
    return f"{c.lower()}.{s.lower()}.{t.lower()}"


def _quote_sql_literal(val: str) -> str:
    return "'" + str(val).replace("'", "''") + "'"


def _build_predicate(pairs: List[Tuple[str, str]], schema_col: str = "schema_name", table_col: str = "table_name") -> str:
    clauses = [
        f"({schema_col} = {_quote_sql_literal(s)} AND {table_col} = {_quote_sql_literal(t)})"
        for s, t in pairs
    ]
    return " OR ".join(clauses) if clauses else "1=0"


def fetch_live_state(provider, table_names: List[str]) -> Dict[str, ObjectState]:
    """Fetch live object types (TABLE/VIEW) and tags for a list of table names."""
    state: Dict[str, ObjectState] = {}
    if not table_names:
        return state

    grouped: Dict[str, List[Tuple[str, str]]] = {}
    for name in table_names:
        norm = _normalize_fqn(name)
        c, s, t = _split_fqn(name)
        state[norm] = ObjectState(display=name, exists=False, object_type="TABLE", tags={}, all_tags={})
        grouped.setdefault(c, []).append((s, t))

    for catalog, pairs in grouped.items():
        # 1. Fetch table types
        type_predicate = _build_predicate(pairs, schema_col="table_schema", table_col="table_name")
        type_query = (
            f"SELECT table_schema, table_name, table_type "
            f"FROM {catalog}.information_schema.tables "
            f"WHERE {type_predicate}"
        )
        try:
            resp = provider.client.statement_execution.execute_statement(
                statement=type_query,
                warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                wait_timeout="30s",
            )
            if resp.result and resp.result.data_array:
                for row in resp.result.data_array:
                    s_name, t_name, t_type = row[0], row[1], (row[2] or "")
                    key = f"{catalog.lower()}.{s_name.lower()}.{t_name.lower()}"
                    if key in state:
                        state[key].exists = True
                        state[key].object_type = "VIEW" if str(t_type).upper() == "VIEW" else "TABLE"
        except Exception as e:
            logger.warning(f"Could not query table types from {catalog}: {e}")

        # 2. Fetch tags
        tag_predicate = _build_predicate(pairs, schema_col="schema_name", table_col="table_name")
        tag_query = (
            f"SELECT schema_name, table_name, tag_name, tag_value "
            f"FROM {catalog}.information_schema.table_tags "
            f"WHERE {tag_predicate}"
        )
        try:
            resp = provider.client.statement_execution.execute_statement(
                statement=tag_query,
                warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                wait_timeout="30s",
            )
            if resp.result and resp.result.data_array:
                for row in resp.result.data_array:
                    s_name, t_name, tag_name, tag_val = row[0], row[1], row[2], row[3]
                    key = f"{catalog.lower()}.{s_name.lower()}.{t_name.lower()}"
                    if key in state:
                        val_str = "" if tag_val is None else str(tag_val)
                        state[key].all_tags[tag_name] = val_str
                        if not any(tag_name.startswith(p) for p in HIDDEN_DISPLAY_PREFIXES):
                            state[key].tags[tag_name] = val_str
        except Exception as e:
            logger.warning(f"Could not query table tags from {catalog}: {e}")

    return state


def fetch_tag_vocabulary(
    provider,
    table_names: List[str],
    keys_of_interest: List[str],
    dataset_values: Optional[List[str]] = None,
    dataset_key: str = DATASET_KEY,
) -> TagVocabulary:
    """Fetch tag usage frequencies and dataset members across involved catalogs."""
    vocabulary = TagVocabulary()
    if not table_names or not keys_of_interest:
        return vocabulary

    catalogs = sorted({_split_fqn(name)[0] for name in table_names})
    key_list_sql = ", ".join(_quote_sql_literal(k) for k in keys_of_interest)

    for catalog in catalogs:
        # Aggregated tag value usage
        vocab_query = (
            f"SELECT tag_name, tag_value, count(*) AS n "
            f"FROM {catalog}.information_schema.table_tags "
            f"WHERE tag_name IN ({key_list_sql}) "
            f"GROUP BY tag_name, tag_value"
        )
        try:
            resp = provider.client.statement_execution.execute_statement(
                statement=vocab_query,
                warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                wait_timeout="30s",
            )
            if resp.result and resp.result.data_array:
                vocabulary.available = True
                for row in resp.result.data_array:
                    tag_name, tag_val, count = row[0], row[1], row[2]
                    bucket = vocabulary.values.setdefault(str(tag_name), {})
                    val_str = "" if tag_val is None else str(tag_val)
                    bucket[val_str] = bucket.get(val_str, 0) + int(count or 0)
        except Exception as e:
            logger.warning(f"Could not query tag vocabulary for {catalog}: {e}")

        # Dataset membership
        if dataset_values:
            val_list_sql = ", ".join(_quote_sql_literal(v) for v in dataset_values if v)
            if val_list_sql:
                mem_query = (
                    f"SELECT catalog_name, schema_name, table_name, tag_value "
                    f"FROM {catalog}.information_schema.table_tags "
                    f"WHERE tag_name IN ({_quote_sql_literal(dataset_key)}, 'data_set') "
                    f"AND tag_value IN ({val_list_sql})"
                )
                try:
                    resp = provider.client.statement_execution.execute_statement(
                        statement=mem_query,
                        warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                        wait_timeout="30s",
                    )
                    if resp.result and resp.result.data_array:
                        for row in resp.result.data_array:
                            c_name, s_name, t_name, ds_val = row[0], row[1], row[2], row[3]
                            if ds_val:
                                members = vocabulary.dataset_members.setdefault(str(ds_val), set())
                                members.add(f"{str(c_name).lower()}.{str(s_name).lower()}.{str(t_name).lower()}")
                except Exception as e:
                    logger.warning(f"Could not query dataset membership for {catalog}: {e}")

    return vocabulary


def build_tag_plan(
    tables_payload: List[Dict[str, Any]],
    live_state: Dict[str, ObjectState],
) -> TagPlan:
    """Build a deterministic TagPlan from requested desired tags and live state."""
    plan = TagPlan()

    for item in tables_payload:
        full_name = item.get("table", "")
        if not full_name:
            continue
        norm = _normalize_fqn(full_name)
        state = live_state.get(norm)
        if not state:
            state = ObjectState(display=full_name, exists=False, object_type="TABLE", tags={}, all_tags={})

        if not state.exists:
            if full_name not in plan.missing_objects:
                plan.missing_objects.append(full_name)

        desired_raw = item.get("desired_tags") or {}
        desired = {
            str(k): str(v)
            for k, v in desired_raw.items()
            if k and not any(str(k).startswith(p) for p in HIDDEN_DISPLAY_PREFIXES)
        }
        # Dataset partition tags ('dataset', 'data_set') are structural grouping metadata;
        # if present in live state and omitted in a partial edit, preserve them so tables are not orphaned.
        for ds_key in ("dataset", "data_set"):
            if ds_key in state.tags and ds_key not in desired:
                desired[ds_key] = state.tags[ds_key]

        current = dict(state.tags)

        # Diff calculation
        diff = ObjectDiff(
            table=full_name,
            object_type=state.object_type,
            exists=state.exists,
            before=current,
            after=desired,
        )
        plan.diffs[norm] = diff

        # Statements calculation
        obj_type = state.object_type.upper()
        set_tags = {k: v for k, v in desired.items() if current.get(k) != v}
        unset_keys = [k for k in current if k not in desired]

        if set_tags:
            pairs = ", ".join(f"'{_escape_sql_literal(k)}' = '{_escape_sql_literal(v)}'" for k, v in set_tags.items())
            sql = f"ALTER {obj_type} {full_name} SET TAGS ({pairs});"
            plan.statement_plans.append(
                StatementPlan(
                    table=full_name,
                    object_type=obj_type,
                    operation="set",
                    tags=set_tags,
                    sql=sql,
                    is_noop=False,
                )
            )
        elif not unset_keys and desired:
            plan.statement_plans.append(
                StatementPlan(
                    table=full_name,
                    object_type=obj_type,
                    operation="set",
                    tags={},
                    sql="",
                    is_noop=True,
                    noop_reason="values already match",
                )
            )

        if unset_keys:
            keys_str = ", ".join(f"'{_escape_sql_literal(k)}'" for k in unset_keys)
            sql = f"ALTER {obj_type} {full_name} UNSET TAGS ({keys_str});"
            plan.statement_plans.append(
                StatementPlan(
                    table=full_name,
                    object_type=obj_type,
                    operation="unset",
                    keys=unset_keys,
                    sql=sql,
                    is_noop=False,
                )
            )

    return plan
