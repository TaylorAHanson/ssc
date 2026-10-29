"""The ``metadata_observed`` snapshot of Unity Catalog (ADR-0006 D2). Workstream B.

Bulk reads of ``information_schema`` (catalogs, schemata, tables, columns and the
four ``*_tags`` views) through ``get_governance_uc_provider()``. ``""`` tag values
are stored as None. Only the reconciler calls into UC; the UI reads the snapshot.

Every read goes through ``system.information_schema`` filtered by catalog, which
lists what the identity can only ``BROWSE`` (``<catalog>.information_schema``
would need ``USE CATALOG``), and an object in a catalog that doesn't exist yet
comes back as not found rather than as an error.

* ``sweep`` reads whole catalogs (five statements per catalog, the last four at
  once, and a few catalogs in parallel) and commits per catalog. Tables, columns and
  their tags come back by external link, which has no size limit. Where those links
  can't be downloaded they're read inline, and a result over the warehouse's inline
  limit (25 MiB) is read again in halves, by schema and then by table, until each
  part fits. A catalog whose query fails, or whose own
  ``catalogs`` row is missing (the identity lost ``BROWSE``), is reported and its
  rows are left untouched: information_schema filters silently, so an empty read
  is never taken to mean "everything was dropped". For the same reason a read that
  would mark more than METADATA_MAX_MISSING_PERCENT of a catalog missing isn't
  saved, and a catalog the scope matched before that no longer shows up is reported
  rather than dropped from the sweep.
* ``refresh`` and ``poll_new_objects`` read specific objects. Neither commits. A
  refresh also reads the catalog's own row and its relations' schemas, so losing
  sight of either leaves the objects as they were instead of marking them dropped.

Skipped everywhere: the ``information_schema`` schema itself, and relations whose
name starts with ``__`` (Databricks-managed materializations), with their columns.
Names that ``ObjectKey.parse`` refuses can't be managed and aren't stored.
"""
from __future__ import annotations

import contextlib
import contextvars
import fnmatch
import logging
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
CATALOG_READ_KEY = "observed.catalog_read_at"
CATALOG_ATTEMPT_KEY = "observed.catalog_attempt_at"
GOVERNED_TAGS_KEY = "observed.governed_tags"
NEW_OBJECT_WATERMARK_KEY = "observed.new_object_watermark"

# Never walked when the scope falls back to every visible catalog.
_EXCLUDED_CATALOGS = ("system", "samples", "hive_metastore", "__databricks_internal")
_EXCLUDED_CATALOG_TYPES = ("SYSTEM_CATALOG", "INTERNAL_CATALOG", "DELTASHARING_CATALOG")
_SKIPPED_SCHEMAS = ("information_schema",)
_HIDDEN_RELATION_PREFIX = "__"

# Catalogs read concurrently; writes stay on the caller's thread, in catalog order.
_READ_WORKERS = 4
# Warehouse statements a sweep runs at once, across every catalog it's reading.
_SWEEP_STATEMENTS = threading.BoundedSemaphore(8)
# Parts of one over-limit inline read run at once.
_PART_WORKERS = 4
# After an external-link download fails, sweeps read inline for this long before trying links again.
_EXTERNAL_LINKS_RETRY_SECONDS = 3600
# A warehouse read at least this long is logged with its row count.
_SLOW_READ_SECONDS = 30
_UPSERT_CHUNK = 1000
# Stored rows fetched per batch while a sweep compares a catalog with what it read.
_COMPARE_CHUNK = 5000
# (schema, table) pairs per information_schema statement in a targeted refresh.
_REFRESH_CHUNK = 200
# Catalog names per IN (...) list in the new-object poll.
_POLL_CATALOG_CHUNK = 100
# fqn_keys per IN (...) list against Lakebase.
_KEY_CHUNK = 500
# Longest full_data_type kept; a longer one (a wide nested struct) is stored as its bare type name.
_DATA_TYPE_MAX = 500
# A full read may always mark this many objects missing, whatever METADATA_MAX_MISSING_PERCENT says.
_MISSING_FLOOR = 500
# A catalog read started this long ago that never finished counts as unread (the check was killed).
# Longer than any one catalog's read, so a check still running isn't reported as interrupted.
_INTERRUPTED_AFTER = timedelta(minutes=20)
_INTERRUPTED = "interrupted: the check reading this catalog stopped before it finished"
_VANISHED = (
    "no longer listed in Unity Catalog: it was dropped or renamed, or the app lost BROWSE on it. Its objects "
    "are kept as they were last read"
)

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
# What a sweep compares to decide a stored row is unchanged (and so isn't rewritten).
_COMPARED_FIELDS = tuple(name for name in _SNAPSHOT_FIELDS if name != "observed_at")


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


def _statement(
    provider, sql: str, label: Optional[str] = None, *, external_links: bool = False, gated: bool = False,
    max_rows: Optional[int] = None,
):
    """``sql``'s ``StatementResult``; ``gated`` waits for one of the sweep's warehouse slots. Raises on failure."""
    from app.providers.databricks.uc_metadata import statement_result

    with _SWEEP_STATEMENTS if gated else contextlib.nullcontext():
        began = time.monotonic()
        result = statement_result(
            provider.client, sql, _warehouse_id(provider), label, external_links=external_links, max_rows=max_rows,
        )
    took = time.monotonic() - began
    if label and took >= _SLOW_READ_SECONDS:
        logger.info("Observed read: %s — %d row(s) in %d s.", label, len(result.rows), took)
    return result


def _rows(
    provider, sql: str, label: Optional[str] = None, *, gated: bool = False, max_rows: Optional[int] = None,
) -> List[list]:
    """Every row of ``sql`` (polled to completion, all result chunks). Raises on failure."""
    return [list(r) for r in _statement(provider, sql, label, gated=gated, max_rows=max_rows).rows or []]


def _max_rows_per_read() -> Optional[int]:
    """METADATA_MAX_ROWS_PER_READ for one statement of a sweep; None when 0 (no limit)."""
    limit = _int_setting("METADATA_MAX_ROWS_PER_READ", 2_000_000)
    return limit if limit > 0 else None


_external_links_lock = threading.Lock()
_external_links_retry_at = 0.0
_external_links_warned = False


def _external_links_usable() -> bool:
    return time.monotonic() >= _external_links_retry_at


def _external_links_failed(label: str, error: Exception) -> None:
    """Sweeps read inline for a while; the first failure in this process is a warning."""
    global _external_links_retry_at, _external_links_warned
    with _external_links_lock:
        _external_links_retry_at = time.monotonic() + _EXTERNAL_LINKS_RETRY_SECONDS
        first, _external_links_warned = not _external_links_warned, True
    (logger.warning if first else logger.info)(
        "Observed read: %s could not be fetched by external link (%s); reading it inline, as are large reads for "
        "the next %d min. Inline reads of a big catalog are slower: check that the app can reach the "
        "workspace's cloud storage.",
        label, error, _EXTERNAL_LINKS_RETRY_SECONDS // 60,
    )


def _over_inline_limit(error: Exception) -> bool:
    """The statement failed because its result is over the warehouse's inline size limit (25 MiB)."""
    text = str(error).lower()
    return "inline byte limit" in text or "disposition=external_links" in text


@dataclass(frozen=True)
class _Part:
    """Part of a catalog's relation-level read: these schemas, and optionally these tables of one schema."""

    schemas: Tuple[str, ...]
    tables: Optional[Tuple[str, ...]] = None


_Tables = Callable[[], Dict[str, List[str]]]


def _halves(part: _Part, tables: Optional[_Tables]) -> List[_Part]:
    """``part`` split in two: by schema, then by the tables of a single schema; [] when it can't split.

    ``tables()`` (schema -> its relations' names) is only called to split a single schema.
    """
    if len(part.schemas) > 1:
        mid = len(part.schemas) // 2
        return [_Part(part.schemas[:mid]), _Part(part.schemas[mid:])]
    names = part.tables if part.tables is not None else tuple((tables() if tables else {}).get(part.schemas[0], ()))
    if len(names) < 2:
        return []
    mid = len(names) // 2
    return [_Part(part.schemas, names[:mid]), _Part(part.schemas, names[mid:])]


def _rows_in_parts(
    provider, label: str, build: Callable[[Optional[_Part]], str], schemas: Sequence[str],
    tables: Optional[_Tables] = None, *, gated: bool = False, max_rows: Optional[int] = None,
) -> Tuple[List[list], int]:
    """Rows of ``build(None)`` and how many parts that took (0 = one statement).

    A result over the inline limit is read again in halves, a few parts at a time, until
    each fits. Halving goes by schema, then (when ``tables`` lists a schema's tables) by
    table. Any other failure, a single table still over the limit, or parts adding up to
    more than ``max_rows``, raises.
    """
    from app.providers.databricks.uc_metadata import ResultTooLarge

    try:
        return _rows(provider, build(None), label, gated=gated, max_rows=max_rows), 0
    except Exception as e:  # noqa: BLE001 - re-raised unless it's the size limit
        if not _over_inline_limit(e):
            raise
        first = e
    pending = _halves(_Part(tuple(schemas)), tables) if schemas else []
    if not pending:
        raise first
    logger.info("Observed read: %s is over the warehouse's inline result limit; reading it in parts.", label)
    rows: List[list] = []
    statements = 0
    running: Dict[Future, _Part] = {}
    with ThreadPoolExecutor(max_workers=_PART_WORKERS, thread_name_prefix="observed-part") as pool:
        def submit(part: _Part) -> None:
            nonlocal statements
            statements += 1
            sql = build(part)
            running[pool.submit(
                contextvars.copy_context().run, _rows, provider, sql, f"{label} (part {statements})", gated=gated,
                max_rows=max_rows,
            )] = part

        for part in pending:
            submit(part)
        try:
            while running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    part = running.pop(future)
                    try:
                        rows.extend(future.result())
                    except Exception as e:  # noqa: BLE001 - re-raised unless it's the size limit
                        halves = _halves(part, tables) if _over_inline_limit(e) else []
                        if not halves:
                            raise
                        for half in halves:
                            submit(half)
                    if max_rows is not None and len(rows) > max_rows:
                        raise ResultTooLarge(len(rows), max_rows)
        finally:
            for future in running:
                future.cancel()
    logger.info("Observed read: %s read in %d statement(s).", label, statements)
    return rows, statements


@dataclass
class _StatementRead:
    """One statement's rows and how they were read, for a sweep's per-catalog timing line."""

    label: str
    rows: List[list]
    seconds: float
    how: str


def _read_statement(
    provider, catalog: str, label: str, build: Callable[[Optional[_Part]], str], schemas: Sequence[str] = (),
    tables: Optional[_Tables] = None, *, sweep: bool = False, large: bool = False,
) -> _StatementRead:
    """Rows of ``build(None)``: by external link for a ``large`` read in a ``sweep``, else inline (in parts if need be).

    A failed external-link read is read again inline.
    """
    from app.providers.databricks.uc_metadata import ExternalLinksUnavailable

    began = time.monotonic()
    after = ""
    max_rows = _max_rows_per_read() if sweep else None
    if sweep and large and _external_links_usable():
        try:
            result = _statement(provider, build(None), f"{catalog} {label}", external_links=True, gated=True,
                                max_rows=max_rows)
        except ExternalLinksUnavailable as e:
            _external_links_failed(f"{catalog} {label}", e)
            after = " after external links failed"
        else:
            how = "external links" if result.chunks <= 1 else f"external links, {result.chunks} chunks"
            return _StatementRead(label, [list(r) for r in result.rows], time.monotonic() - began, how)
    rows, parts = _rows_in_parts(provider, f"{catalog} {label}", build, schemas, tables, gated=sweep,
                                 max_rows=max_rows)
    how = (f"inline in {parts} parts" if parts else "inline") + after
    return _StatementRead(label, rows, time.monotonic() - began, how)


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


def _read(
    provider, info: str, catalog: str, targets: _Targets, observed_at: datetime, *, sweep: bool = False,
) -> Dict[str, dict]:
    """Snapshot rows keyed by fqn_key for what ``targets`` selects (relations filtered to exact pairs).

    After the catalog and schema rows, the relation, column and tag statements run at
    once. A ``sweep`` (a whole-catalog read) fetches the relation-level ones by external
    link, counts every statement against the sweep's warehouse cap, and logs how each
    statement was read.
    """
    began = time.monotonic()
    sql = _statements(info, catalog, targets)
    rows: Dict[str, dict] = {}
    reads: List[_StatementRead] = []

    def read(name: str, label: str, schemas: Sequence[str] = (), tables: Optional[_Tables] = None,
             large: bool = False) -> _StatementRead:
        return _read_statement(
            provider, catalog, label, lambda part: _statements(info, catalog, targets, part)[name], schemas, tables,
            sweep=sweep, large=large,
        )

    containers = read("containers", "schemas") if "containers" in sql else None
    if containers is not None:
        reads.append(containers)
    for r in containers.rows if containers is not None else []:
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

    def read_relations() -> Tuple[_StatementRead, Dict[Tuple[str, str], dict], Dict[str, List[str]]]:
        """The relations read; its rows by (schema, table) lowercased; and schema -> its relations' names."""
        done = read("relations", "relations", schemas, large=True)
        found: Dict[Tuple[str, str], dict] = {}
        for r in done.rows:
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
            found[pair] = _snapshot_row(
                securable_type_for(table_type), key, observed_at,
                table_type=table_type or None, owner=owner or None, comment=normalize_value(comment),
                created=_ts(created), last_altered=_ts(altered),
            )
        names: Dict[str, List[str]] = defaultdict(list)
        for schema, table in sorted(found):
            names[schema].append(table)
        return done, found, names

    # A thread per statement: the column and tag reads may wait on the relations read to split by table.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="observed-statement") as pool:
        def submit(fn: Callable, *args: Any, **kwargs: Any) -> Future:
            return pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)

        relations_job = columns_job = tags_job = relation_tags_job = None
        if "relations" in sql:
            relations_job = submit(read_relations)

            def tables() -> Dict[str, List[str]]:
                return relations_job.result()[2]

            columns_job = submit(read, "columns", "columns", schemas, tables, large=True)
            relation_tags_job = submit(read, "relation_tags", "relation tags", schemas, tables, large=True)
        if "tags" in sql:
            tags_job = submit(read, "tags", "catalog and schema tags")

        # (schema, table) lowercased -> the relation's row, for attaching columns and tags.
        relations: Dict[Tuple[str, str], dict] = {}
        if relations_job is not None:
            relations_read, relations, _ = relations_job.result()
            reads.append(relations_read)
            rows.update((row["fqn_key"], row) for row in relations.values())
        if columns_job is not None:
            columns_read = columns_job.result()
            reads.append(columns_read)
            for r in columns_read.rows:
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
        tag_rows: List[list] = []
        for job in (tags_job, relation_tags_job):
            if job is not None:
                reads.append(job.result())
                tag_rows += reads[-1].rows

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

    if sweep:
        took = time.monotonic() - began
        logger.info(
            "Observed read: %s — %s; %d s in all.", catalog,
            "; ".join(f"{r.label} {len(r.rows):,} rows {r.seconds:.0f} s ({r.how})" for r in reads), took,
            extra={"data": {"catalog": catalog, "seconds": round(took, 1), "statements": [
                {"statement": r.label, "rows": len(r.rows), "seconds": round(r.seconds, 1), "read": r.how}
                for r in reads
            ]}},
        )
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


class TooManyMissing(RuntimeError):
    """A full read would mark more of a catalog missing than METADATA_MAX_MISSING_PERCENT allows."""


def _int_setting(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _missing_allowed(present: int) -> int:
    percent = min(max(_int_setting("METADATA_MAX_MISSING_PERCENT", 5), 0), 100)
    return present if percent >= 100 else max(_MISSING_FLOOR, present * percent // 100)


def _write_catalog(db: Session, catalog: str, rows: Dict[str, dict], observed_at: datetime) -> Tuple[int, int]:
    """Save a whole-catalog read; returns (rows written, rows newly missing).

    Only new or changed rows are written. An unchanged row keeps its stored
    ``observed_at``; reads report the catalog's last successful read instead (see
    ``_seen_at``), since even a one-column UPDATE rewrites every row in Postgres. Rows
    saved after this read began (a targeted refresh meanwhile) are newer than it:
    they're neither overwritten nor marked missing. Raises ``TooManyMissing``, having
    written nothing, when the read is missing too much of what was there.
    """
    keep: Set[str] = set()
    missing: List[str] = []
    present = 0
    stored = db.execute(
        select(_T.c.fqn_key, _T.c.observed_at, *(_T.c[name] for name in _COMPARED_FIELDS))
        .where(_T.c.catalog == catalog)
        .execution_options(yield_per=_COMPARE_CHUNK)
    )
    for row in stored:
        present += bool(row.present)
        if row.observed_at is not None and row.observed_at >= observed_at:
            keep.add(row.fqn_key)
            continue
        read = rows.get(row.fqn_key)
        if read is None:
            if row.present:
                missing.append(row.fqn_key)
        elif all(getattr(row, name) == read[name] for name in _COMPARED_FIELDS):
            keep.add(row.fqn_key)
    allowed = _missing_allowed(present)
    if len(missing) > allowed:
        raise TooManyMissing(
            f"this check would have marked {len(missing):,} of its {present:,} objects "
            f"({len(missing) * 100 / present:.1f}%) as dropped, more than Max share missing per check allows "
            f"({allowed:,}), so nothing from it was saved"
        )
    changed = [row for key, row in rows.items() if key not in keep]
    _upsert(db, changed)
    _mark_keys_missing(db, missing, observed_at)
    return len(changed), len(missing)


def _note_catalog_read(db: Session, catalog: str, at: datetime) -> None:
    """Record a catalog's successful whole read, which stands in for its unchanged rows' observed_at."""
    _set_state(db, CATALOG_READ_KEY, {**(_get_state(db, CATALOG_READ_KEY) or {}), catalog: at.isoformat()})


def _catalog_reads(db: Session) -> Dict[str, datetime]:
    """Catalog -> when its last successful whole read began."""
    return _timestamps(db, CATALOG_READ_KEY)


def _timestamps(db: Session, key: str) -> Dict[str, datetime]:
    found = {c: _parse_iso(at) for c, at in (_get_state(db, key) or {}).items()}
    return {c: at for c, at in found.items() if at is not None}


def _note_attempts(db: Session, catalogs: Sequence[str], at: datetime) -> None:
    """Record that a sweep is about to read these catalogs, so one killed mid-read is reported unread."""
    _set_state(db, CATALOG_ATTEMPT_KEY, {
        **(_get_state(db, CATALOG_ATTEMPT_KEY) or {}), **{c: at.isoformat() for c in catalogs},
    })


def _interrupted(db: Session, now: Optional[datetime] = None) -> List[str]:
    """Catalogs whose latest read attempt began a while ago and never finished, as when a deploy kills the check."""
    before = (now or _utcnow()) - _INTERRUPTED_AFTER
    reads = _catalog_reads(db)
    return sorted(
        c for c, at in _timestamps(db, CATALOG_ATTEMPT_KEY).items()
        if at < before and (c not in reads or reads[c] < at)
    )


def vanished_catalogs(db: Session, found: Sequence[str]) -> List[str]:
    """Catalogs read before and still in METADATA_RECONCILE_CATALOGS that the scope no longer finds.

    A pattern-matched catalog that disappears (dropped, renamed, or no longer visible
    to the app) would otherwise drop out of the sweep silently. Listed until its last
    successful read is older than METADATA_ORPHAN_RETENTION_DAYS.
    """
    if not scope.is_explicit():
        return []
    since = _utcnow() - timedelta(days=max(_int_setting("METADATA_ORPHAN_RETENTION_DAYS", 30), 1))
    names = set(found)
    return sorted(c for c, at in _catalog_reads(db).items() if c not in names and at >= since and scope.manages(c))


def confirmed_reads(db: Session) -> Dict[str, datetime]:
    """Catalog -> when its last successful whole read began, for catalogs not currently unread."""
    unread = set(unread_catalogs(db))
    return {c: at for c, at in _catalog_reads(db).items() if c not in unread}


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
    logger.info("Observed read: %s started.", catalog)
    rows = _read(provider, "system.information_schema", catalog, _Targets(), observed_at, sweep=True)
    if catalog not in rows:
        raise RuntimeError(
            f"information_schema returned no row for catalog '{catalog}'; it doesn't exist or the "
            "sweep identity lacks BROWSE on it"
        )
    return _CatalogRead(catalog=catalog, observed_at=observed_at, rows=rows)


def _read_ahead(provider, catalogs: Sequence[str]) -> Iterator[Tuple[str, Optional[_CatalogRead], Optional[Exception]]]:
    """(catalog, read, error) in catalog order, reading a few catalogs ahead in parallel.

    No more catalogs are read ahead than are read at once, so a finished read only waits
    while the one before it is written; the reads held in memory stay bounded.
    """
    with ThreadPoolExecutor(max_workers=max(1, min(_READ_WORKERS, len(catalogs))), thread_name_prefix="observed") as pool:
        queue: deque = deque()
        remaining = iter(catalogs)
        # A fresh copy per read, so its lines carry the run's log context (one context can't be entered twice).
        def submit(name: str):
            return pool.submit(contextvars.copy_context().run, _read_catalog, provider, name)

        for catalog in remaining:
            queue.append((catalog, submit(catalog)))
            if len(queue) >= _READ_WORKERS:
                break
        while queue:
            catalog, future = queue.popleft()
            following = next(remaining, None)
            if following is not None:
                queue.append((following, submit(following)))
            try:
                yield catalog, future.result(), None
            except Exception as e:  # noqa: BLE001 - reported per catalog
                yield catalog, None, e


def sweep(
    db: Session, *, provider=None, catalogs: Optional[List[str]] = None, vanished: Sequence[str] = (),
) -> SweepResult:
    """Refresh the snapshot for whole catalogs; objects no longer seen get ``present=False``.

    ``catalogs=None`` means ``scope_catalogs()``, and then ``vanished`` defaults to
    ``vanished_catalogs()``; vanished catalogs are reported as failed. Commits after
    each catalog, so a failure later in the sweep keeps what was already written.
    """
    started = time.monotonic()
    provider = _provider(provider)
    targets = list(dict.fromkeys(c.strip().lower() for c in (catalogs or []) if c and c.strip()))
    if catalogs is None:
        targets = scope_catalogs(provider)
        vanished = vanished_catalogs(db, targets)
    result = SweepResult()
    sweep_started_at = _utcnow()
    read_s = write_s = 0.0
    for catalog in vanished:
        result.failed_catalogs[catalog] = _VANISHED
        logger.warning("Observed sweep: catalog %s is %s.", catalog, _VANISHED)

    if targets:
        _note_attempts(db, targets, sweep_started_at)
        db.commit()
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
                written, missing = _write_catalog(db, catalog, read.rows, read.observed_at)
                _note_catalog_read(db, catalog, read.observed_at)
                db.commit()
            except TooManyMissing as e:
                result.failed_catalogs[catalog] = str(e)[:1000]
                logger.warning("Observed sweep: catalog %s wasn't saved, %s.", catalog, e)
            except Exception as e:  # noqa: BLE001 - reported per catalog
                db.rollback()
                result.failed_catalogs[catalog] = f"snapshot write failed: {e}"[:1000]
                logger.error("Observed sweep: could not write catalog %s: %s", catalog, e, exc_info=True)
            else:
                result.catalogs.append(catalog)
                result.objects_seen += len(read.rows)
                result.objects_missing += missing
                logger.info(
                    "Observed sweep: %s — %d object(s) seen, %d changed, %d newly missing; saved in %d s.",
                    catalog, len(read.rows), written, missing, time.monotonic() - began,
                )
            write_s += time.monotonic() - began
            waited = time.monotonic()

    began = time.monotonic()
    _refresh_governed_tags(db, provider)
    governed_ms = int((time.monotonic() - began) * 1000)
    began = time.monotonic()
    result.took_ms = int((time.monotonic() - started) * 1000)
    result.read_ms = int(read_s * 1000)
    result.write_ms = int(write_s * 1000)
    previous = _get_state(db, LAST_SWEEP_KEY) or {}
    # Every catalog whose latest sweep failed, including ones this sweep didn't try.
    unread = list(dict.fromkeys(
        [c for c in previous.get("unread_catalogs") or [] if c not in targets] + sorted(result.failed_catalogs)
    ))
    previous_reasons = previous.get("unread_reasons") or {}
    unread_reasons = {c: previous_reasons[c] for c in unread if c in previous_reasons and c not in result.failed_catalogs}
    unread_reasons.update({c: str(err) for c, err in result.failed_catalogs.items()})
    state = {
        # Only a sweep that refreshed something moves the "last sweep" time.
        "at": sweep_started_at.isoformat() if result.catalogs or not targets else previous.get("at"),
        "attempted_at": sweep_started_at.isoformat(),
        "catalogs": result.catalogs,
        "failed_catalogs": result.failed_catalogs,
        "unread_catalogs": unread,
        "unread_reasons": unread_reasons,
        "objects_seen": result.objects_seen,
        "objects_missing": result.objects_missing,
        "took_ms": result.took_ms,
    }
    _set_state(db, LAST_SWEEP_KEY, state)
    db.commit()
    write_s += time.monotonic() - began
    result.write_ms = int(write_s * 1000)
    logger.info(
        "Observed sweep finished in %d ms (%d ms waiting on Unity Catalog, %d ms saving, %d ms listing governed "
        "tags): %d catalog(s), %d object(s), %d newly missing, %d failed.",
        result.took_ms, result.read_ms, result.write_ms, governed_ms, len(result.catalogs), result.objects_seen,
        result.objects_missing, len(result.failed_catalogs),
    )
    return result


# --- Targeted refresh and new-object poll -------------------------------------------


def _guess_type(key: ObjectKey) -> str:
    return {"catalog": "catalog", "schema": "schema", "column": "column"}.get(key.level, "table")


def _schema_key(key: ObjectKey) -> str:
    return ObjectKey(catalog=key.catalog, schema_name=key.schema_name).fqn_key


def _seen_before(db: Session, fqn_keys: Sequence[str]) -> Set[str]:
    """Those of ``fqn_keys`` whose stored row says the object was there at the last read."""
    seen: Set[str] = set()
    for chunk in _chunks(sorted(set(fqn_keys)), _KEY_CHUNK):
        seen.update(db.execute(
            select(_T.c.fqn_key).where(_T.c.fqn_key.in_(list(chunk)), _T.c.present.is_(True))
        ).scalars())
    return seen


def refresh(db: Session, keys: Iterable[ObjectKey], *, provider=None) -> Dict[str, ObservedState]:
    """    Targeted re-read of specific objects (and a relation's columns) right before applying.

    A column key re-reads its whole relation. Returns the state of every requested
    key plus the re-read relations and columns (including columns this read found
    dropped, now ``present=False``), and the catalog and schema rows read alongside;
    a key that wasn't found comes back with ``present=False`` (its row, if any, is
    marked missing; no row is created for an object never seen).

    Left out of the result, with their rows untouched: keys in a catalog whose read
    failed or whose own row didn't come back although it was seen before (dropped, or
    the app lost BROWSE), and keys in a schema seen before that didn't come back. The
    full sweep, with its own guard, is what confirms those were dropped. Flushes; the
    caller commits.
    """
    keys = list(keys)
    if not keys:
        return {}
    provider = _provider(provider)
    by_catalog: Dict[str, Tuple[_Targets, Dict[str, ObjectKey]]] = {}
    for key in keys:
        catalog = key.catalog.lower()
        targets, requested = by_catalog.setdefault(catalog, (_Targets(catalog=True, schemas=set(), relations=set()), {}))
        if key.level == "catalog":
            requested[key.fqn_key] = key
        elif key.level == "schema":
            targets.schemas.add(key.schema_name.lower())  # type: ignore[union-attr]
            requested[key.fqn_key] = key
        else:
            relation = key.relation_key()
            targets.schemas.add(relation.schema_name.lower())  # type: ignore[union-attr]
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
        expected = {catalog} | {_schema_key(key) for key in requested.values() if key.level != "catalog"}
        gone = _seen_before(db, [k for k in expected if k not in found])
        if catalog in gone:
            logger.warning(
                "Observed refresh: catalog %s didn't come back from Unity Catalog although it was read before (dropped, "
                "or the app lost BROWSE on it); its %d object(s) are left as they were.", catalog, len(requested),
            )
            continue
        withheld = {k for k, key in requested.items() if key.level != "catalog" and _schema_key(key) in gone}
        if withheld:
            logger.warning(
                "Observed refresh: %d object(s) in catalog %s are in schemas that didn't come back from Unity Catalog "
                "(%s); they're left as they were until a full check confirms the schemas were dropped.",
                len(withheld), catalog, ", ".join(sorted(gone)),
            )
        reads.append(({k: v for k, v in requested.items() if k not in withheld}, found, now))

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
        # Their schema and catalog rows too, so a table in a catalog the sweep couldn't read is still listed under them.
        parents: Dict[str, ObjectKey] = {}
        for key in found.values():
            for parent in (_object_key(key.catalog), _object_key(key.catalog, key.schema_name)):
                if parent is not None:
                    parents.setdefault(parent.fqn_key, parent)
        refresh(db, [*found.values(), *parents.values()], provider=provider)
    newest = min(newest, now)
    if newest > watermark:
        _set_state(db, NEW_OBJECT_WATERMARK_KEY, {"at": newest.isoformat()})
    db.flush()
    if found:
        logger.info("New-object poll: %d relation(s) created or altered since %s.", len(found), watermark.isoformat())
    return sorted(found)


# --- Reads --------------------------------------------------------------------------


def _state(row: MetadataObservedModel, catalog_read_at: Optional[datetime] = None) -> ObservedState:
    """``observed_at`` is when the object was last seen: a sweep that found it unchanged doesn't rewrite the row."""
    seen_at = row.observed_at
    if row.present and catalog_read_at is not None and (seen_at is None or catalog_read_at > seen_at):
        seen_at = catalog_read_at
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
        observed_at=seen_at,
        missing_since=row.missing_since,
    )


def get_observed(db: Session, fqn_keys: Iterable[str]) -> Dict[str, ObservedState]:
    """Snapshot rows keyed by fqn_key (present or not); keys never observed are absent."""
    keys = sorted({k for k in fqn_keys if k})
    out: Dict[str, ObservedState] = {}
    reads = _catalog_reads(db) if keys else {}
    for chunk in _chunks(keys, _KEY_CHUNK):
        for row in db.scalars(select(MetadataObservedModel).where(MetadataObservedModel.fqn_key.in_(list(chunk)))):
            out[row.fqn_key] = _state(row, reads.get(row.catalog))
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
    reads = _catalog_reads(db)
    return [_state(row, reads.get(row.catalog)) for row in db.scalars(query)]


def governed_tags(db: Session) -> Dict[str, List[str]]:
    """Governed tag key -> allowed values, cached by the sweep (empty list = any value)."""
    tags = (_get_state(db, GOVERNED_TAGS_KEY) or {}).get("tags") or {}
    return {str(k): [str(v) for v in (values or [])] for k, values in tags.items()}


def last_sweep_at(db: Session) -> Optional[datetime]:
    """When the last sweep that refreshed at least one catalog started (naive UTC)."""
    return _parse_iso((_get_state(db, LAST_SWEEP_KEY) or {}).get("at"))


def unread_catalogs(db: Session) -> List[str]:
    """Managed catalogs whose latest sweep couldn't read them, or was killed reading them, so their snapshot may be incomplete."""
    found = list((_get_state(db, LAST_SWEEP_KEY) or {}).get("unread_catalogs") or []) + _interrupted(db)
    return [c for c in dict.fromkeys(found) if scope.manages(c)]


def explain_read_error(error: Optional[str]) -> str:
    """A short reason for the browse banner; the raw warehouse text is kept in Sync history."""
    text = str(error or "").strip()
    lower = text.lower()
    if lower.startswith("interrupted:"):
        return "The last check stopped while reading this catalog (for example, the app restarted). The next check reads it again."
    if "max share missing per check" in lower:
        return (
            "The last check found far fewer objects than before, so it wasn’t trusted and nothing was saved. If they "
            "really were dropped, raise Max share missing per check in Admin → Settings and run Check now."
        )
    if lower.startswith("no longer listed"):
        return "This catalog no longer shows up in Unity Catalog: it was dropped or renamed, or the app lost access to it."
    if "one read may hold" in lower:
        return (
            "This catalog has more tables, columns or tags than one check may hold in memory, so it wasn’t read. "
            "Raise Largest catalog read (rows) in Admin → Settings only if the app has memory to spare."
        )
    if "browse" in lower:
        return "The app doesn’t have permission to see this catalog (it needs BROWSE)."
    if "inline byte limit" in lower:
        return "This catalog is too large to read from the warehouse in one go."
    if any(word in lower for word in (
        "timeout", "timed out", "canceled", "cancelled", "temporarily_unavailable", "still running", "still pending",
    )):
        return "The warehouse ran out of time reading this catalog. A large catalog can take several minutes; the next scheduled check will try again."
    if "snapshot write failed" in lower:
        return "The catalog was read from Unity Catalog but couldn’t be saved here."
    if not text:
        return "The last check couldn’t read this catalog."
    return text[:240]


def unread_reasons(db: Session) -> Dict[str, str]:
    """Managed unread catalogs -> a short reason the browse page can show."""
    state = _get_state(db, LAST_SWEEP_KEY) or {}
    stored = state.get("unread_reasons") or {}
    interrupted = set(_interrupted(db))
    return {
        c: explain_read_error(stored.get(c) or (_INTERRUPTED if c in interrupted else None)) for c in unread_catalogs(db)
    }
