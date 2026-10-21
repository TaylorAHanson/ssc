"""The ``metadata_observed`` snapshot of Unity Catalog (ADR-0006 D2). Workstream B.

Bulk reads of ``information_schema`` (catalogs, schemata, tables, columns and the
four ``*_tags`` views) through ``get_governance_uc_provider()``. ``""`` tag values
are stored as None. Only the reconciler calls into UC; the UI reads the snapshot.

Every read goes through ``system.information_schema`` filtered by catalog, which
lists what the identity can only ``BROWSE`` (``<catalog>.information_schema``
would need ``USE CATALOG``), and an object in a catalog that doesn't exist yet
comes back as not found rather than as an error.

* ``sweep`` reads whole catalogs (five statements per catalog, a few catalogs in
  parallel) and commits per catalog. A table, column or tag read whose result is
  over the warehouse's inline limit (25 MiB) is read again in halves, by schema and
  then by table, until each part fits. A catalog whose query fails, or whose own
  ``catalogs`` row is missing (the identity lost ``BROWSE``), is reported and its
  rows are left untouched: information_schema filters silently, so an empty read
  is never taken to mean "everything was dropped".
* ``refresh`` and ``poll_new_objects`` read specific objects. Neither commits.

Skipped everywhere: the ``information_schema`` schema itself, and relations whose
name starts with ``__`` (Databricks-managed materializations), with their columns.
Names that ``ObjectKey.parse`` refuses can't be managed and aren't stored.
"""
from __future__ import annotations

import fnmatch
import logging
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from dateutil import parser as date_parser
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_scan_catalogs, settings
from app.db.metadata import MetadataObservedModel, MetadataSyncStateModel
from app.services.metadata import scope
from app.services.metadata.sql import sql_string_literal
from app.services.metadata.types import ObjectKey, ObservedState, normalize_value

logger = logging.getLogger(__name__)

LAST_SWEEP_KEY = "observed.last_sweep_at"
GOVERNED_TAGS_KEY = "observed.governed_tags"
NEW_OBJECT_WATERMARK_KEY = "observed.new_object_watermark"

# Never walked when the scope falls back to every visible catalog.
_EXCLUDED_CATALOGS = ("system", "samples", "hive_metastore", "__databricks_internal")
_EXCLUDED_CATALOG_TYPES = ("SYSTEM_CATALOG", "INTERNAL_CATALOG", "DELTASHARING_CATALOG")
_SKIPPED_SCHEMAS = ("information_schema",)
_HIDDEN_RELATION_PREFIX = "__"

# Catalogs read concurrently; writes stay on the caller's thread, in catalog order.
_READ_WORKERS = 4
_UPSERT_CHUNK = 1000
# (schema, table) pairs per information_schema statement in a targeted refresh.
_REFRESH_CHUNK = 200
# Catalog names per IN (...) list in the new-object poll.
_POLL_CATALOG_CHUNK = 100
# fqn_keys per IN (...) list against Lakebase.
_KEY_CHUNK = 500
# Longest full_data_type kept; a longer one (a wide nested struct) is stored as its bare type name.
_DATA_TYPE_MAX = 500

_TABLE_TYPES = {
    "VIEW": "view",
    "MATERIALIZED_VIEW": "materialized_view",
    "STREAMING_TABLE": "streaming_table",
    "METRIC_VIEW": "metric_view",
}

_SNAPSHOT_FIELDS = (
    "securable_type", "catalog", "schema_name", "object_name", "column_name", "parent_key", "display_name",
    "table_type", "data_type", "ordinal_position", "owner", "comment", "tags", "present", "created",
    "last_altered", "observed_at", "missing_since",
)


class SweepResult(BaseModel):
    catalogs: List[str] = Field(default_factory=list)
    objects_seen: int = 0
    # Objects in the swept catalogs that this sweep newly marked present=False.
    objects_missing: int = 0
    failed_catalogs: Dict[str, str] = Field(default_factory=dict)
    took_ms: int = 0
    # Time spent waiting on information_schema reads, and writing the snapshot.
    read_ms: int = 0
    write_ms: int = 0


# --- Small helpers ------------------------------------------------------------------


def securable_type_for(table_type: Optional[str]) -> str:
    """UC ``table_type`` -> securable type: views by kind, everything else (MANAGED, EXTERNAL, ...) is a table."""
    return _TABLE_TYPES.get(str(table_type or "").strip().upper().replace(" ", "_"), "table")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(value: Any) -> Optional[datetime]:
    """A timestamp from the statement API (ISO text) as naive UTC, like every other column here."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        try:
            dt = date_parser.isoparse(text)
        except (ValueError, OverflowError):
            try:
                dt = date_parser.parse(text)
            except (ValueError, OverflowError):
                logger.debug("Unparseable timestamp from information_schema: %r", value)
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _in_list(values: Iterable[str]) -> str:
    return ", ".join(sql_string_literal(v) for v in sorted(set(values)))


def _skipped_schema(schema: Any) -> bool:
    return str(schema or "").lower() in _SKIPPED_SCHEMAS


def _hidden_relation(name: Any) -> bool:
    return str(name or "").startswith(_HIDDEN_RELATION_PREFIX)


def _object_key(catalog: Any, schema: Any = None, obj: Any = None, column: Any = None) -> Optional[ObjectKey]:
    name = ".".join(str(p) for p in (catalog, schema, obj) if p not in (None, ""))
    try:
        return ObjectKey.parse(name, None if column in (None, "") else str(column))
    except ValueError:
        return None


def _snapshot_row(securable_type: str, key: ObjectKey, observed_at: datetime, **values: Any) -> dict:
    parent = key.parent()
    row = {
        "fqn_key": key.fqn_key,
        "securable_type": securable_type,
        "catalog": key.catalog.lower(),
        "schema_name": key.schema_name,
        "object_name": key.object_name,
        "column_name": key.column_name,
        "parent_key": parent.fqn_key if parent else None,
        "display_name": key.display,
        "table_type": None,
        "data_type": None,
        "ordinal_position": None,
        "owner": None,
        "comment": None,
        "tags": {},
        "present": True,
        "created": None,
        "last_altered": None,
        "observed_at": observed_at,
        "missing_since": None,
    }
    row.update(values)
    return row


# --- UC access ----------------------------------------------------------------------


def _provider(provider):
    if provider is not None:
        return provider
    from app.core.workspaces import get_governance_uc_provider

    return get_governance_uc_provider()


def _warehouse_id(provider) -> str:
    getter = getattr(provider, "get_config", None)
    configured = getter("warehouse_id") if callable(getter) else None
    return configured or settings.DATABRICKS_WAREHOUSE_ID


def _rows(provider, sql: str) -> List[list]:
    """Every row of ``sql`` (polled to completion, all result chunks). Raises on failure."""
    from app.providers.databricks.uc_metadata import _run_statement

    return [list(r) for r in _run_statement(provider.client, sql, _warehouse_id(provider)) or []]


def _over_inline_limit(error: Exception) -> bool:
    """The statement failed because its result is over the warehouse's inline size limit (25 MiB)."""
    text = str(error).lower()
    return "inline byte limit" in text or "disposition=external_links" in text


@dataclass(frozen=True)
class _Part:
    """Part of a catalog's relation-level read: these schemas, and optionally these tables of one schema."""

    schemas: Tuple[str, ...]
    tables: Optional[Tuple[str, ...]] = None


def _halves(part: _Part, tables: Optional[Dict[str, List[str]]]) -> List[_Part]:
    """``part`` split in two: by schema, then by the tables of a single schema; [] when it can't split."""
    if len(part.schemas) > 1:
        mid = len(part.schemas) // 2
        return [_Part(part.schemas[:mid]), _Part(part.schemas[mid:])]
    names = part.tables if part.tables is not None else tuple((tables or {}).get(part.schemas[0], ()))
    if len(names) < 2:
        return []
    mid = len(names) // 2
    return [_Part(part.schemas, names[:mid]), _Part(part.schemas, names[mid:])]


def _rows_in_parts(
    provider, label: str, build: Callable[[Optional[_Part]], str], schemas: Sequence[str],
    tables: Optional[Dict[str, List[str]]] = None,
) -> List[list]:
    """Rows of ``build(None)``; a result over the inline limit is read again in halves until each fits.

    Halving goes by schema, then (when ``tables`` lists a schema's tables) by table. Any
    other failure, or a single table still over the limit, raises.
    """
    try:
        return _rows(provider, build(None))
    except Exception as e:  # noqa: BLE001 - re-raised unless it's the size limit
        if not _over_inline_limit(e):
            raise
        first = e
    logger.info("Observed read: %s is over the warehouse's inline result limit; reading it in parts.", label)
    rows: List[list] = []
    statements = 0
    pending = _halves(_Part(tuple(schemas)), tables) if schemas else []
    if not pending:
        raise first
    while pending:
        part = pending.pop()
        statements += 1
        try:
            rows.extend(_rows(provider, build(part)))
        except Exception as e:  # noqa: BLE001 - re-raised unless it's the size limit
            halves = _halves(part, tables) if _over_inline_limit(e) else []
            if not halves:
                raise
            pending.extend(halves)
    logger.info("Observed read: %s read in %d statement(s).", label, statements)
    return rows


def scope_catalogs(provider=None) -> List[str]:
    """METADATA_RECONCILE_CATALOGS, else SCAN_CATALOGS, else every catalog the provider can see.

    Names are lowercased (as information_schema and ``fqn_key`` report them). Patterns
    (``*_dev``, ``*``) are matched against the visible catalogs, which skip system,
    sample, internal and Delta Sharing catalogs.
    """
    names = scope.patterns() or list(dict.fromkeys(c.strip().lower() for c in get_scan_catalogs() if c.strip()))
    globs = [n for n in names if scope.is_pattern(n)]
    if names and not globs:
        return names
    visible = _visible_catalogs(provider)
    if not names:
        return visible
    matched = [c for c in visible if any(fnmatch.fnmatchcase(c, g) for g in globs)]
    return list(dict.fromkeys([n for n in names if not scope.is_pattern(n)] + matched))


def _visible_catalogs(provider=None) -> List[str]:
    provider = _provider(provider)
    visible: List[str] = []
    for info in provider.client.catalogs.list(include_browse=True):
        name = str(getattr(info, "name", "") or "").strip().lower()
        kind = getattr(info, "catalog_type", None)
        kind = str(getattr(kind, "value", kind) or "").upper()
        if not name or name in _EXCLUDED_CATALOGS or kind in _EXCLUDED_CATALOG_TYPES:
            continue
        visible.append(name)
    return list(dict.fromkeys(visible))


# --- Statements ---------------------------------------------------------------------


@dataclass
class _Targets:
    """What to read from one catalog. ``None`` = everything at that level, empty = nothing."""

    catalog: bool = True
    schemas: Optional[Set[str]] = None
    relations: Optional[Set[Tuple[str, str]]] = None


def _not_skipped(column: str) -> str:
    return f"{column} NOT IN ({_in_list(_SKIPPED_SCHEMAS)})"


def _relation_filter(pairs: Optional[Set[Tuple[str, str]]], schema_col: str, table_col: str) -> str:
    # Schema and table filtered separately, as in uc_metadata; the over-selection is
    # discarded by the exact-key match when rows are assembled.
    if pairs is None:
        return ""
    return (
        f" AND {schema_col} IN ({_in_list(s for s, _ in pairs)})"
        f" AND {table_col} IN ({_in_list(t for _, t in pairs)})"
    )


def _part_filter(part: Optional[_Part], schema_col: str, table_col: str) -> str:
    if part is None:
        return ""
    out = f" AND {schema_col} IN ({_in_list(part.schemas)})"
    if part.tables is not None:
        out += f" AND {table_col} IN ({_in_list(part.tables)})"
    return out


def _statements(info: str, catalog: str, targets: _Targets, part: Optional[_Part] = None) -> Dict[str, str]:
    """containers / tags (catalog and schema) and relations / columns / relation_tags statements for one catalog.

    ``part`` narrows the relation-level statements to some schemas or tables, for a
    catalog whose result is too big to read at once.
    """
    lit = sql_string_literal(catalog)
    null = "CAST(NULL AS STRING)"
    out: Dict[str, str] = {}

    containers: List[str] = []
    tags: List[str] = []
    if targets.catalog:
        containers.append(
            f"SELECT 'catalog' AS kind, catalog_name, {null} AS schema_name, catalog_owner AS owner, comment, "
            f"created, last_altered FROM {info}.catalogs WHERE catalog_name = {lit}"
        )
        tags.append(
            f"SELECT 'catalog' AS kind, {null} AS schema_name, {null} AS table_name, {null} AS column_name, "
            f"tag_name, tag_value FROM {info}.catalog_tags WHERE catalog_name = {lit}"
        )
    if targets.schemas is None or targets.schemas:
        where = f"catalog_name = {lit} AND {_not_skipped('schema_name')}"
        if targets.schemas is not None:
            where += f" AND schema_name IN ({_in_list(targets.schemas)})"
        containers.append(
            f"SELECT 'schema' AS kind, catalog_name, schema_name, schema_owner AS owner, comment, "
            f"created, last_altered FROM {info}.schemata WHERE {where}"
        )
        tags.append(
            f"SELECT 'schema' AS kind, schema_name, {null} AS table_name, {null} AS column_name, "
            f"tag_name, tag_value FROM {info}.schema_tags WHERE {where}"
        )
    if targets.relations is None or targets.relations:
        rel = _relation_filter(targets.relations, "table_schema", "table_name") + _part_filter(part, "table_schema", "table_name")
        tag_rel = _relation_filter(targets.relations, "schema_name", "table_name") + _part_filter(part, "schema_name", "table_name")
        out["relations"] = (
            f"SELECT table_schema, table_name, table_type, table_owner, comment, created, last_altered "
            f"FROM {info}.tables WHERE table_catalog = {lit} AND {_not_skipped('table_schema')}{rel}"
        )
        out["columns"] = (
            f"SELECT table_schema, table_name, column_name, "
            f"CASE WHEN length(full_data_type) > {_DATA_TYPE_MAX} THEN data_type ELSE full_data_type END, "
            f"ordinal_position, comment "
            f"FROM {info}.columns WHERE table_catalog = {lit} AND {_not_skipped('table_schema')}{rel}"
        )
        out["relation_tags"] = (
            f"SELECT 'table' AS kind, schema_name, table_name, {null} AS column_name, tag_name, tag_value "
            f"FROM {info}.table_tags WHERE catalog_name = {lit} AND {_not_skipped('schema_name')}{tag_rel}"
            f" UNION ALL "
            f"SELECT 'column' AS kind, schema_name, table_name, column_name, tag_name, tag_value "
            f"FROM {info}.column_tags WHERE catalog_name = {lit} AND {_not_skipped('schema_name')}{tag_rel}"
        )
    if containers:
        out["containers"] = " UNION ALL ".join(containers)
    if tags:
        out["tags"] = " UNION ALL ".join(tags)
    return out


def _read(provider, info: str, catalog: str, targets: _Targets, observed_at: datetime) -> Dict[str, dict]:
    """Snapshot rows keyed by fqn_key for what ``targets`` selects (relations filtered to exact pairs)."""
    sql = _statements(info, catalog, targets)
    rows: Dict[str, dict] = {}
    # (schema, table) lowercased -> the relation's row, for attaching columns and tags.
    relations: Dict[Tuple[str, str], dict] = {}

    for r in _rows(provider, sql["containers"]) if "containers" in sql else []:
        if len(r) < 7:
            continue
        kind, cat_name, schema, owner, comment, created, altered = r[:7]
        if kind == "schema" and _skipped_schema(schema):
            continue
        key = _object_key(cat_name or catalog, schema if kind == "schema" else None)
        if key is None:
            continue
        rows[key.fqn_key] = _snapshot_row(
            "schema" if kind == "schema" else "catalog", key, observed_at,
            owner=owner or None, comment=normalize_value(comment), created=_ts(created), last_altered=_ts(altered),
        )

    # A relation-level read too big for one result is split by these schemas, then by table.
    schemas = sorted(
        {s for s, _ in targets.relations} if targets.relations is not None
        else {str(row["schema_name"]).lower() for row in rows.values() if row["securable_type"] == "schema"}
    )

    def read(name: str, tables: Optional[Dict[str, List[str]]] = None) -> List[list]:
        return _rows_in_parts(
            provider, f"{catalog} {name}", lambda part: _statements(info, catalog, targets, part)[name], schemas, tables,
        )

    # schema -> its relations' names, once read, for splitting the column and tag reads by table.
    tables: Dict[str, List[str]] = defaultdict(list)
    if "relations" in sql:
        for r in read("relations"):
            if len(r) < 7:
                continue
            schema, table, table_type, owner, comment, created, altered = r[:7]
            pair = (str(schema).lower(), str(table).lower())
            if _skipped_schema(schema) or _hidden_relation(table):
                continue
            if targets.relations is not None and pair not in targets.relations:
                continue
            key = _object_key(catalog, schema, table)
            if key is None:
                continue
            row = _snapshot_row(
                securable_type_for(table_type), key, observed_at,
                table_type=table_type or None, owner=owner or None, comment=normalize_value(comment),
                created=_ts(created), last_altered=_ts(altered),
            )
            rows[key.fqn_key] = relations[pair] = row
        for schema, table in sorted(relations):
            tables[schema].append(table)
        for r in read("columns", tables):
            if len(r) < 6:
                continue
            schema, table, column, data_type, ordinal, comment = r[:6]
            relation = relations.get((str(schema).lower(), str(table).lower()))
            key = _object_key(catalog, schema, table, column) if relation is not None else None
            if key is None:
                continue
            rows[key.fqn_key] = _snapshot_row(
                "column", key, observed_at,
                table_type=relation["table_type"], data_type=data_type or None,
                ordinal_position=_int(ordinal), comment=normalize_value(comment),
            )

    tag_rows = _rows(provider, sql["tags"]) if "tags" in sql else []
    if "relation_tags" in sql:
        tag_rows += read("relation_tags", tables)
    for r in tag_rows:
        if len(r) < 6:
            continue
        kind, schema, table, column, tag_name, tag_value = r[:6]
        if not tag_name:
            continue
        if kind == "catalog":
            row = rows.get(catalog)
        elif kind == "schema":
            row = rows.get(f"{catalog}.{str(schema).lower()}")
        else:
            row = relations.get((str(schema).lower(), str(table).lower()))
            if row is not None and kind == "column":
                key = _object_key(catalog, schema, table, column)
                row = rows.get(key.fqn_key) if key is not None else None
        if row is not None:
            row["tags"][str(tag_name)] = normalize_value(tag_value)
    return rows


# --- Lakebase writes ----------------------------------------------------------------

_T = MetadataObservedModel.__table__


def _upsert(db: Session, rows: Sequence[dict]) -> None:
    """Insert or overwrite snapshot rows by fqn_key, in chunks (SQLite and Postgres ON CONFLICT)."""
    if not rows:
        return
    dialect = db.get_bind().dialect.name
    if dialect in ("postgresql", "sqlite"):
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        stmt = insert(_T)
        stmt = stmt.on_conflict_do_update(
            index_elements=[_T.c.fqn_key], set_={name: stmt.excluded[name] for name in _SNAPSHOT_FIELDS},
        )
        for chunk in _chunks(list(rows), _UPSERT_CHUNK):
            db.execute(stmt, list(chunk))
        return
    for row in rows:
        db.merge(MetadataObservedModel(**row))
    db.flush()


def _missing_values(at: datetime) -> dict:
    return {"present": False, "missing_since": func.coalesce(_T.c.missing_since, at)}


def _mark_catalog_missing(db: Session, catalog: str, observed_at: datetime) -> int:
    """Rows of a swept catalog that the sweep didn't write (older observed_at) are no longer there."""
    result = db.execute(
        update(_T)
        .where(
            _T.c.catalog == catalog,
            _T.c.present.is_(True),
            or_(_T.c.observed_at.is_(None), _T.c.observed_at < observed_at),
        )
        .values(**_missing_values(observed_at))
    )
    return int(result.rowcount or 0)


def _mark_keys_missing(db: Session, fqn_keys: Iterable[str], at: datetime) -> None:
    keys = sorted(set(fqn_keys))
    for chunk in _chunks(keys, _KEY_CHUNK):
        db.execute(
            update(_T).where(_T.c.fqn_key.in_(list(chunk)), _T.c.present.is_(True)).values(**_missing_values(at))
        )


def _mark_stale_columns_missing(db: Session, relation_keys: Iterable[str], at: datetime) -> List[str]:
    """Columns of re-read relations that this read didn't see; returns their fqn_keys."""
    stale: List[str] = []
    for chunk in _chunks(sorted(set(relation_keys)), _KEY_CHUNK):
        stale += db.execute(
            select(_T.c.fqn_key).where(
                _T.c.parent_key.in_(list(chunk)),
                _T.c.securable_type == "column",
                _T.c.present.is_(True),
                or_(_T.c.observed_at.is_(None), _T.c.observed_at < at),
            )
        ).scalars().all()
    _mark_keys_missing(db, stale, at)
    return stale


def _get_state(db: Session, key: str) -> Optional[dict]:
    row = db.get(MetadataSyncStateModel, key)
    return row.value if row is not None and isinstance(row.value, dict) else None


def _set_state(db: Session, key: str, value: dict) -> None:
    row = db.get(MetadataSyncStateModel, key)
    now = _utcnow()
    if row is None:
        db.add(MetadataSyncStateModel(key=key, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
    db.flush()


def _refresh_governed_tags(db: Session, provider) -> None:
    """Cache governed key -> allowed values; a failure keeps the previous cache."""
    from app.workflows import tag_governed

    try:
        tags = tag_governed.list_governed_keys(provider)
    except Exception as e:  # noqa: BLE001 - advisory; UC still enforces on write
        logger.warning("Observed sweep: could not read governed tags (keeping the cached list): %s", e)
        return
    _set_state(db, GOVERNED_TAGS_KEY, {
        "at": _utcnow().isoformat(),
        "tags": {t.key: list(t.allowed_values or []) for t in tags if t.key},
    })


# --- Sweep --------------------------------------------------------------------------


@dataclass
class _CatalogRead:
    catalog: str
    observed_at: datetime
    rows: Dict[str, dict] = field(default_factory=dict)


def _read_catalog(provider, catalog: str) -> _CatalogRead:
    observed_at = _utcnow()
    rows = _read(provider, "system.information_schema", catalog, _Targets(), observed_at)
    if catalog not in rows:
        raise RuntimeError(
            f"information_schema returned no row for catalog '{catalog}'; it doesn't exist or the "
            "sweep identity lacks BROWSE on it"
        )
    return _CatalogRead(catalog=catalog, observed_at=observed_at, rows=rows)


def _read_ahead(provider, catalogs: Sequence[str]) -> Iterator[Tuple[str, Optional[_CatalogRead], Optional[Exception]]]:
    """(catalog, read, error) in catalog order, reading a few catalogs ahead in parallel."""
    with ThreadPoolExecutor(max_workers=max(1, min(_READ_WORKERS, len(catalogs))), thread_name_prefix="observed") as pool:
        queue: deque = deque()
        remaining = iter(catalogs)
        for catalog in remaining:
            queue.append((catalog, pool.submit(_read_catalog, provider, catalog)))
            if len(queue) >= _READ_WORKERS * 2:
                break
        while queue:
            catalog, future = queue.popleft()
            following = next(remaining, None)
            if following is not None:
                queue.append((following, pool.submit(_read_catalog, provider, following)))
            try:
                yield catalog, future.result(), None
            except Exception as e:  # noqa: BLE001 - reported per catalog
                yield catalog, None, e


def sweep(db: Session, *, provider=None, catalogs: Optional[List[str]] = None) -> SweepResult:
    """Refresh the snapshot for whole catalogs; objects no longer seen get ``present=False``.

    ``catalogs`` defaults to ``scope_catalogs()``. Commits after each catalog, so a
    failure later in the sweep keeps what was already written.
    """
    started = time.monotonic()
    provider = _provider(provider)
    targets = list(dict.fromkeys(c.strip().lower() for c in (catalogs or []) if c and c.strip()))
    if not catalogs:
        targets = scope_catalogs(provider)
    result = SweepResult()
    sweep_started_at = _utcnow()
    read_s = write_s = 0.0

    if targets:
        waited = time.monotonic()
        for catalog, read, error in _read_ahead(provider, targets):
            read_s += time.monotonic() - waited
            if error is not None or read is None:
                result.failed_catalogs[catalog] = str(error)[:1000]
                logger.warning("Observed sweep: catalog %s failed, its snapshot is unchanged: %s", catalog, error)
                waited = time.monotonic()
                continue
            began = time.monotonic()
            try:
                _upsert(db, list(read.rows.values()))
                missing = _mark_catalog_missing(db, catalog, read.observed_at)
                db.commit()
            except Exception as e:  # noqa: BLE001 - reported per catalog
                db.rollback()
                result.failed_catalogs[catalog] = f"snapshot write failed: {e}"[:1000]
                logger.error("Observed sweep: could not write catalog %s: %s", catalog, e, exc_info=True)
            else:
                result.catalogs.append(catalog)
                result.objects_seen += len(read.rows)
                result.objects_missing += missing
                logger.info(
                    "Observed sweep: %s — %d object(s) seen, %d newly missing.", catalog, len(read.rows), missing,
                )
            write_s += time.monotonic() - began
            waited = time.monotonic()

    began = time.monotonic()
    _refresh_governed_tags(db, provider)
    result.took_ms = int((time.monotonic() - started) * 1000)
    result.read_ms = int(read_s * 1000)
    result.write_ms = int(write_s * 1000)
    previous = _get_state(db, LAST_SWEEP_KEY) or {}
    state = {
        # Only a sweep that refreshed something moves the "last sweep" time.
        "at": sweep_started_at.isoformat() if result.catalogs or not targets else previous.get("at"),
        "attempted_at": sweep_started_at.isoformat(),
        "catalogs": result.catalogs,
        "failed_catalogs": result.failed_catalogs,
        "objects_seen": result.objects_seen,
        "objects_missing": result.objects_missing,
        "took_ms": result.took_ms,
    }
    _set_state(db, LAST_SWEEP_KEY, state)
    db.commit()
    write_s += time.monotonic() - began
    result.write_ms = int(write_s * 1000)
    logger.info(
        "Observed sweep finished in %d ms: %d catalog(s), %d object(s), %d newly missing, %d failed.",
        result.took_ms, len(result.catalogs), result.objects_seen, result.objects_missing, len(result.failed_catalogs),
    )
    return result


# --- Targeted refresh and new-object poll -------------------------------------------


def _guess_type(key: ObjectKey) -> str:
    return {"catalog": "catalog", "schema": "schema", "column": "column"}.get(key.level, "table")


def refresh(db: Session, keys: Iterable[ObjectKey], *, provider=None) -> Dict[str, ObservedState]:
    """    Targeted re-read of specific objects (and a relation's columns) right before applying.

    A column key re-reads its whole relation. Returns the state of every requested
    key plus the re-read relations and columns (including columns this read found
    dropped, now ``present=False``); a key that wasn't found comes back
    with ``present=False`` (its row, if any, is marked missing; no row is created
    for an object never seen). Keys in a catalog whose read failed are left out of
    the result and their rows are untouched. Flushes; the caller commits.
    """
    keys = list(keys)
    if not keys:
        return {}
    provider = _provider(provider)
    by_catalog: Dict[str, Tuple[_Targets, Dict[str, ObjectKey]]] = {}
    for key in keys:
        catalog = key.catalog.lower()
        targets, requested = by_catalog.setdefault(catalog, (_Targets(catalog=False, schemas=set(), relations=set()), {}))
        if key.level == "catalog":
            targets.catalog = True
            requested[key.fqn_key] = key
        elif key.level == "schema":
            targets.schemas.add(key.schema_name.lower())  # type: ignore[union-attr]
            requested[key.fqn_key] = key
        else:
            relation = key.relation_key()
            targets.relations.add((relation.schema_name.lower(), relation.object_name.lower()))  # type: ignore[union-attr]
            requested[key.fqn_key] = key
            requested.setdefault(relation.fqn_key, relation)

    # Every Unity Catalog read happens before the first write: on SQLite a write holds the
    # database lock until the caller commits, and these reads can take many seconds.
    reads: List[Tuple[Dict[str, ObjectKey], Dict[str, dict], datetime]] = []
    for catalog, (targets, requested) in by_catalog.items():
        now = _utcnow()
        # Relations go in chunks; the catalog and schema rows ride along with the first.
        pairs = sorted(targets.relations or [])
        batches = [
            _Targets(catalog=targets.catalog and i == 0, schemas=targets.schemas if i == 0 else set(), relations=set(chunk))
            for i, chunk in enumerate(_chunks(pairs, _REFRESH_CHUNK))
        ] or [_Targets(catalog=targets.catalog, schemas=targets.schemas, relations=set())]
        found: Dict[str, dict] = {}
        try:
            for batch in batches:
                found.update(_read(provider, "system.information_schema", catalog, batch, now))
        except Exception as e:  # noqa: BLE001 - the caller skips what it couldn't observe
            logger.warning("Observed refresh: could not read %d object(s) in catalog %s: %s", len(requested), catalog, e)
            continue
        reads.append((requested, found, now))

    result_keys: Dict[str, ObjectKey] = {}
    for requested, found, now in reads:
        _upsert(db, list(found.values()))
        _mark_keys_missing(db, set(requested) - set(found), now)
        relation_keys = [k for k, key in requested.items() if key.level == "relation"]
        dropped_columns = _mark_stale_columns_missing(db, relation_keys, now)
        result_keys.update(requested)
        for fqn_key, row in found.items():
            result_keys.setdefault(fqn_key, ObjectKey(
                catalog=row["catalog"], schema_name=row["schema_name"],
                object_name=row["object_name"], column_name=row["column_name"],
            ))
        for fqn_key in dropped_columns:
            result_keys.setdefault(fqn_key, ObjectKey.from_fqn_key(fqn_key))
    db.flush()

    states = get_observed(db, result_keys)
    for fqn_key, key in result_keys.items():
        if fqn_key not in states:
            states[fqn_key] = ObservedState(fqn_key=fqn_key, key=key, securable_type=_guess_type(key), present=False)
    return states


def _timestamp_literal(at: datetime) -> str:
    return "TIMESTAMP " + sql_string_literal(at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))


def poll_new_objects(db: Session, *, provider=None) -> List[str]:
    """Relation fqn_keys created or altered since the stored watermark (which this advances).

    The first run only sets the watermark to now and returns nothing: the sweep
    covers objects that already exist. Found objects are refreshed into the
    snapshot. The watermark never moves past now, so a bogus future timestamp in
    UC can't stall the poll. Flushes; the caller commits (with its enqueue).
    """
    now = _utcnow()
    state = _get_state(db, NEW_OBJECT_WATERMARK_KEY)
    watermark = _parse_iso((state or {}).get("at"))
    if watermark is None:
        _set_state(db, NEW_OBJECT_WATERMARK_KEY, {"at": now.isoformat()})
        logger.info("New-object poll: no watermark yet, starting from %s.", now.isoformat())
        return []

    provider = _provider(provider)
    catalogs = scope_catalogs(provider)
    if not catalogs:
        return []
    since = _timestamp_literal(watermark)
    found: Dict[str, ObjectKey] = {}
    newest = watermark
    for chunk in _chunks(catalogs, _POLL_CATALOG_CHUNK):
        sql = (
            f"SELECT table_catalog, table_schema, table_name, created, last_altered "
            f"FROM system.information_schema.tables WHERE table_catalog IN ({_in_list(chunk)}) "
            f"AND {_not_skipped('table_schema')} AND (created > {since} OR last_altered > {since})"
        )
        for catalog, schema, table, created, altered in (r[:5] for r in _rows(provider, sql) if len(r) >= 5):
            if _skipped_schema(schema) or _hidden_relation(table):
                continue
            key = _object_key(catalog, schema, table)
            if key is None:
                continue
            found[key.fqn_key] = key
            for stamp in (_ts(created), _ts(altered)):
                if stamp is not None and stamp > newest:
                    newest = stamp

    if found:
        refresh(db, list(found.values()), provider=provider)
    newest = min(newest, now)
    if newest > watermark:
        _set_state(db, NEW_OBJECT_WATERMARK_KEY, {"at": newest.isoformat()})
    db.flush()
    if found:
        logger.info("New-object poll: %d relation(s) created or altered since %s.", len(found), watermark.isoformat())
    return sorted(found)


# --- Reads --------------------------------------------------------------------------


def _state(row: MetadataObservedModel) -> ObservedState:
    return ObservedState(
        fqn_key=row.fqn_key,
        key=ObjectKey(
            catalog=row.catalog, schema_name=row.schema_name, object_name=row.object_name, column_name=row.column_name,
        ),
        securable_type=row.securable_type,
        table_type=row.table_type,
        data_type=row.data_type,
        ordinal_position=row.ordinal_position,
        owner=row.owner,
        comment=row.comment,
        tags=dict(row.tags or {}),
        present=bool(row.present),
        created=row.created,
        last_altered=row.last_altered,
        observed_at=row.observed_at,
        missing_since=row.missing_since,
    )


def get_observed(db: Session, fqn_keys: Iterable[str]) -> Dict[str, ObservedState]:
    """Snapshot rows keyed by fqn_key (present or not); keys never observed are absent."""
    keys = sorted({k for k in fqn_keys if k})
    out: Dict[str, ObservedState] = {}
    for chunk in _chunks(keys, _KEY_CHUNK):
        for row in db.scalars(select(MetadataObservedModel).where(MetadataObservedModel.fqn_key.in_(list(chunk)))):
            out[row.fqn_key] = _state(row)
    return out


def get_columns(db: Session, relation_fqn_key: str, *, include_missing: bool = False) -> List[ObservedState]:
    """A relation's observed columns in ordinal order (dropped columns only with ``include_missing``)."""
    query = select(MetadataObservedModel).where(
        MetadataObservedModel.parent_key == relation_fqn_key.lower(),
        MetadataObservedModel.securable_type == "column",
    )
    if not include_missing:
        query = query.where(MetadataObservedModel.present.is_(True))
    query = query.order_by(MetadataObservedModel.ordinal_position.asc().nulls_last(), MetadataObservedModel.fqn_key)
    return [_state(row) for row in db.scalars(query)]


def governed_tags(db: Session) -> Dict[str, List[str]]:
    """Governed tag key -> allowed values, cached by the sweep (empty list = any value)."""
    tags = (_get_state(db, GOVERNED_TAGS_KEY) or {}).get("tags") or {}
    return {str(k): [str(v) for v in (values or [])] for k, values in tags.items()}


def last_sweep_at(db: Session) -> Optional[datetime]:
    """When the last sweep that refreshed at least one catalog started (naive UTC)."""
    return _parse_iso((_get_state(db, LAST_SWEEP_KEY) or {}).get("at"))
