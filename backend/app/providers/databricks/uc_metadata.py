"""Unity Catalog metadata reads that need only BROWSE.

The SDK's ``tables.get`` / ``schemas.get`` / ``catalogs.get`` require the caller
to be a metastore admin, the object's owner, or to hold ``SELECT`` (plus
``USE CATALOG`` / ``USE SCHEMA``). That is the wrong privilege for a governance
scanner: it needs to *describe* production data, not read it, and asking for
SELECT across an enterprise catalog just to draft a data contract grants far
more than the job requires.

``information_schema`` is the privilege-aware alternative. Databricks filters
its rows to the objects the caller may see, and ``BROWSE`` on the catalog is
sufficient to surface a table, its columns, and its tags — no USE CATALOG,
USE SCHEMA, or SELECT needed. Everything the certification checklist wants
(column names/types/comments, table type, catalog and schema descriptions,
tags) is reachable this way.

Reads are batched per catalog instead of per table, which also collapses what
used to be several SDK round-trips *per table* into a handful of queries.

Caveat worth knowing: because the filtering is silent, a table that is missing
from the results is either nonexistent or invisible to this principal, and the
two are indistinguishable. Callers must not report a missing row as "the table
does not exist" — see :attr:`UcMetadataBatch.visible`.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from app.tools.sql_safety import quote_literal

logger = logging.getLogger(__name__)

# information_schema stores identifiers lowercased (everything except column and
# tag *names*), so all table/schema/catalog keys are compared lowercased.
TableKey = Tuple[str, str, str]

_TERMINAL_STATES = ("SUCCEEDED", "FAILED", "CANCELED", "CLOSED")
_POLL_BUDGET_SECONDS = 300
_POLL_INTERVAL_SECONDS = 2
_PROGRESS_EVERY_SECONDS = 60
# Result chunks of one EXTERNAL_LINKS statement downloaded at once.
_DOWNLOAD_WORKERS = 4
# (connect, read) seconds for one result chunk's download from cloud storage.
_DOWNLOAD_TIMEOUT = (10, 120)
_DOWNLOAD_ATTEMPTS = 2


@dataclass
class ColumnMetadata:
    name: str
    data_type: str
    comment: Optional[str] = None


@dataclass
class TableMetadata:
    full_name: str
    table_type: str = "TABLE"
    comment: Optional[str] = None
    columns: List[ColumnMetadata] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)
    catalog_description: Optional[str] = None
    schema_description: Optional[str] = None

    @property
    def is_view(self) -> bool:
        return "VIEW" in (self.table_type or "").upper()

    @property
    def missing_column_descriptions(self) -> List[str]:
        return [c.name for c in self.columns if not c.comment]


@dataclass
class UcMetadataBatch:
    """Result of one batched metadata read.

    ``visible`` holds the tables information_schema returned. ``not_visible``
    holds the requested names it did not — absent because they don't exist *or*
    because this principal lacks even BROWSE. ``failed_catalogs`` records
    catalogs whose query errored outright, which is a different and louder
    problem than an empty result.
    """

    visible: Dict[str, TableMetadata] = field(default_factory=dict)
    not_visible: List[str] = field(default_factory=list)
    failed_catalogs: Dict[str, str] = field(default_factory=dict)

    def get(self, full_name: str) -> Optional[TableMetadata]:
        return self.visible.get(full_name.lower())


def _split(full_name: str) -> Optional[TableKey]:
    parts = full_name.split(".")
    if len(parts) != 3:
        return None
    return parts[0].lower(), parts[1].lower(), parts[2].lower()


def _in_list(values: Iterable[str]) -> str:
    return ", ".join(quote_literal(v) for v in sorted(set(values)))


def _state_of(response) -> str:
    status = getattr(response, "status", None)
    state = getattr(status, "state", None)
    if state is None:
        return "UNKNOWN"
    return getattr(state, "value", str(state))


def cancel_statement(client, statement_id: Optional[str]) -> None:
    """Best-effort cancel of a statement we've stopped waiting for.

    Abandoning the poll leaves the statement running on the warehouse, and each
    scan that times out adds more, which slows every later query until they too
    time out.
    """
    if not statement_id:
        return
    try:
        client.statement_execution.cancel_execution(statement_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not cancel abandoned statement %s: %s", statement_id, e)


class ExternalLinksUnavailable(RuntimeError):
    """An EXTERNAL_LINKS result couldn't be fetched (cloud storage unreachable, say); INLINE may still work."""


class ResultTooLarge(RuntimeError):
    """A result has more rows than the caller can hold; reading it some other way won't help."""

    def __init__(self, rows: int, limit: int):
        super().__init__(f"the result has {rows:,} rows, more than the {limit:,} one read may hold")
        self.rows = rows
        self.limit = limit


@dataclass
class StatementResult:
    rows: List[list]
    chunks: int = 0
    external_links: bool = False


def _run_statement(
    client, sql: str, warehouse_id: str, label: Optional[str] = None, *, external_links: bool = False,
) -> List[list]:
    """Every result row of ``sql``; see ``statement_result``."""
    return statement_result(client, sql, warehouse_id, label, external_links=external_links).rows


def statement_result(
    client, sql: str, warehouse_id: str, label: Optional[str] = None, *, external_links: bool = False,
    max_rows: Optional[int] = None,
) -> StatementResult:
    """Execute ``sql``, poll it to a terminal state, and return every result row.

    ``execute_statement`` blocks for at most 50s and may return the statement
    still PENDING/RUNNING while the warehouse keeps working on it; on a large
    catalog the ``information_schema.columns`` read can take longer than that.
    Results can also span several chunks. With ``label``, a statement still
    running logs a line every minute, so a slow read can be told from a stuck one.

    INLINE results are capped at 25 MiB. ``external_links`` fetches the result from
    cloud storage instead (no cap), still as JSON_ARRAY so values match the INLINE
    ones; a refused request or failed download raises ``ExternalLinksUnavailable``.
    A result shorter than its manifest says raises rather than returning part of it.
    One with more than ``max_rows`` rows raises ``ResultTooLarge`` before any of it is
    fetched beyond the first chunk.
    """
    from databricks.sdk.errors import DatabricksError

    began = time.monotonic()
    options: Dict[str, Any] = {}
    if external_links:
        from databricks.sdk.service.sql import Disposition, Format

        options = {"disposition": Disposition.EXTERNAL_LINKS, "format": Format.JSON_ARRAY}
    try:
        response = client.statement_execution.execute_statement(
            statement=sql, warehouse_id=warehouse_id, wait_timeout="50s", **options
        )
    except DatabricksError as e:
        if external_links:
            raise ExternalLinksUnavailable(f"the warehouse refused an EXTERNAL_LINKS statement: {e}") from e
        raise
    statement_id = getattr(response, "statement_id", None)

    deadline = time.monotonic() + _POLL_BUDGET_SECONDS
    next_progress = began + _PROGRESS_EVERY_SECONDS
    state = _state_of(response)
    while state not in _TERMINAL_STATES and statement_id:
        now = time.monotonic()
        if now >= deadline:
            cancel_statement(client, statement_id)
            raise RuntimeError(
                f"statement {statement_id} still {state} after {_POLL_BUDGET_SECONDS}s"
            )
        if label and now >= next_progress:
            logger.info(
                "%s: statement %s still %s after %d s (canceled at %d s).",
                label, statement_id, state, now - began, deadline - began,
            )
            next_progress = now + _PROGRESS_EVERY_SECONDS
        time.sleep(_POLL_INTERVAL_SECONDS)
        response = client.statement_execution.get_statement(statement_id)
        state = _state_of(response)

    if state != "SUCCEEDED":
        raise RuntimeError(getattr(response.status, "error", None) or f"statement {state}")

    manifest = getattr(response, "manifest", None)
    total = getattr(manifest, "total_row_count", None)
    if max_rows is not None and isinstance(total, int) and total > max_rows:
        raise ResultTooLarge(total, max_rows)
    out = _result_rows(client, statement_id, getattr(response, "result", None))
    if getattr(manifest, "truncated", None):
        raise RuntimeError(f"statement {statement_id} returned a truncated result")
    if total is not None and total != len(out.rows):
        raise RuntimeError(f"statement {statement_id} has {total} row(s) but only {len(out.rows)} were read")
    return out


def _next_chunk_index(result) -> Optional[int]:
    index = getattr(result, "next_chunk_index", None)
    links = getattr(result, "external_links", None) or []
    if index is None and links:
        index = getattr(links[-1], "next_chunk_index", None)
    return index


def _result_rows(client, statement_id: Optional[str], result) -> StatementResult:
    """Rows of every chunk from ``result`` on, in chunk order; external links download a few at a time."""
    parts: List[Any] = []
    chunks = 0
    pool: Optional[ThreadPoolExecutor] = None
    try:
        while result is not None:
            chunks += 1
            parts.append(getattr(result, "data_array", None) or [])
            for link in getattr(result, "external_links", None) or []:
                if pool is None:
                    pool = ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS, thread_name_prefix="statement-chunk")
                parts.append(pool.submit(_download_chunk, link))
            next_idx = _next_chunk_index(result)
            if next_idx is None or next_idx < 0 or not statement_id:
                break
            result = client.statement_execution.get_statement_result_chunk_n(statement_id, next_idx)
        rows: List[list] = []
        for part in parts:
            rows.extend(part.result() if isinstance(part, Future) else part)
        return StatementResult(rows=rows, chunks=chunks, external_links=pool is not None)
    finally:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)


def _download_chunk(link) -> List[list]:
    """One EXTERNAL_LINKS chunk's JSON_ARRAY rows, fetched without the workspace's credentials.

    The URL is presigned and must not carry an Authorization header. It also holds a
    temporary credential, so neither it nor a requests error that quotes it is kept.
    """
    url = getattr(link, "external_link", None) or ""
    where = f"result chunk {getattr(link, 'chunk_index', '?')} from {urlsplit(url).hostname or 'cloud storage'}"
    if not url:
        raise ExternalLinksUnavailable(f"{where} came back without a download link")
    headers = dict(getattr(link, "http_headers", None) or {})
    problem = ""
    for _ in range(_DOWNLOAD_ATTEMPTS):
        try:
            response = requests.get(url, headers=headers, timeout=_DOWNLOAD_TIMEOUT)
        except requests.RequestException as e:
            problem = type(e).__name__
            continue
        if not response.ok:
            problem = f"HTTP {response.status_code}"
            continue
        try:
            rows = response.json()
        except ValueError:
            rows = None
        if isinstance(rows, list):
            return rows
        problem = "the chunk isn't a JSON array"
    raise ExternalLinksUnavailable(f"could not download {where}: {problem}")


def fetch_uc_metadata(client, full_names: Iterable[str], warehouse_id: str) -> UcMetadataBatch:
    """Batch-read table metadata for ``full_names`` via ``information_schema``.

    ``client`` is a ``WorkspaceClient`` running as the identity whose visibility
    we want to reflect (normally the governance service principal) — results are
    filtered to what that principal can see.
    """
    batch = UcMetadataBatch()

    by_catalog: Dict[str, List[TableKey]] = defaultdict(list)
    requested: Dict[str, str] = {}
    for name in full_names or []:
        key = _split(name)
        if key is None:
            logger.warning("Invalid table name %r; expected catalog.schema.table", name)
            batch.not_visible.append(name)
            continue
        by_catalog[key[0]].append(key)
        requested[".".join(key)] = name

    if not by_catalog:
        return batch

    if not warehouse_id:
        raise ValueError("A SQL warehouse id is required to read information_schema metadata")

    def run(sql: str):
        return _run_statement(client, sql, warehouse_id)

    for catalog, keys in by_catalog.items():
        schemas = _in_list(k[1] for k in keys)
        tables = _in_list(k[2] for k in keys)
        # Filtering on schema and table separately (rather than on pairs) keeps
        # the SQL simple and portable; the small over-selection it can produce is
        # discarded by the exact-key lookup below.
        scope = f"table_schema IN ({schemas}) AND table_name IN ({tables})"
        wanted = {k for k in keys}

        try:
            found: Dict[TableKey, TableMetadata] = {}
            for row in run(
                f"SELECT table_schema, table_name, table_type, comment "
                f"FROM {catalog}.information_schema.tables WHERE {scope}"
            ):
                key = (catalog, str(row[0]).lower(), str(row[1]).lower())
                if key not in wanted:
                    continue
                found[key] = TableMetadata(
                    full_name=".".join(key),
                    table_type=row[2] or "TABLE",
                    comment=row[3],
                )

            for row in run(
                f"SELECT table_schema, table_name, column_name, full_data_type, comment "
                f"FROM {catalog}.information_schema.columns WHERE {scope} "
                f"ORDER BY table_schema, table_name, ordinal_position"
            ):
                key = (catalog, str(row[0]).lower(), str(row[1]).lower())
                meta = found.get(key)
                if meta is not None:
                    meta.columns.append(
                        ColumnMetadata(name=row[2], data_type=row[3] or "unknown", comment=row[4])
                    )

            tag_scope = f"schema_name IN ({schemas}) AND table_name IN ({tables})"
            for row in run(
                f"SELECT schema_name, table_name, tag_name, tag_value "
                f"FROM {catalog}.information_schema.table_tags WHERE {tag_scope}"
            ):
                key = (catalog, str(row[0]).lower(), str(row[1]).lower())
                meta = found.get(key)
                if meta is not None and row[2]:
                    meta.tags[row[2]] = row[3]

            schema_comments: Dict[str, Optional[str]] = {}
            for row in run(
                f"SELECT schema_name, comment FROM {catalog}.information_schema.schemata "
                f"WHERE schema_name IN ({schemas})"
            ):
                schema_comments[str(row[0]).lower()] = row[1]

            catalog_comment = None
            for row in run(
                f"SELECT catalog_name, comment FROM {catalog}.information_schema.catalogs "
                f"WHERE catalog_name = {quote_literal(catalog)}"
            ):
                catalog_comment = row[1]

            for key, meta in found.items():
                meta.catalog_description = catalog_comment
                meta.schema_description = schema_comments.get(key[1])
                batch.visible[meta.full_name] = meta

        except Exception as e:  # noqa: BLE001
            batch.failed_catalogs[catalog] = str(e)
            logger.warning("information_schema metadata read failed for catalog %s: %s", catalog, e)

    for dotted, original in requested.items():
        if dotted not in batch.visible:
            batch.not_visible.append(original)

    if batch.not_visible:
        shown = ", ".join(sorted(batch.not_visible)[:10])
        more = f" (+{len(batch.not_visible) - 10} more)" if len(batch.not_visible) > 10 else ""
        logger.warning(
            "information_schema returned no metadata for %d of %d table(s). They either do not "
            "exist or this principal lacks BROWSE on their catalog: %s%s",
            len(batch.not_visible), len(requested), shown, more,
        )

    return batch
