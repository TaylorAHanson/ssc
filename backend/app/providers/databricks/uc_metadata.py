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
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from app.tools.sql_safety import quote_literal

logger = logging.getLogger(__name__)

# information_schema stores identifiers lowercased (everything except column and
# tag *names*), so all table/schema/catalog keys are compared lowercased.
TableKey = Tuple[str, str, str]


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
        response = client.statement_execution.execute_statement(
            statement=sql, warehouse_id=warehouse_id, wait_timeout="50s"
        )
        state = getattr(response.status.state, "value", str(response.status.state))
        if state != "SUCCEEDED":
            raise RuntimeError(getattr(response.status, "error", None) or f"statement {state}")
        return (response.result.data_array if response.result else None) or []

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
