"""Quick search for the Metadata Manager picker: datasets, catalogs, schemas, tables and views.

One query string covers everything an admin types into the search box:

- ``orders`` — datasets and objects whose name contains it (every word must match)
- ``main.sales.*`` — a glob over the full name, e.g. a whole schema
- ``classification=confidential`` — tagged with that value (``*`` wildcards allowed)
- ``data_owner=*`` — has the tag at all
- ``!data_owner`` — is missing the tag

Names come from the local data-asset cache (synced from the governed catalogs),
so typing is instant; catalogs and schemas are the ones the cached tables sit
in. Tag filters read live tag values from Unity Catalog, since the cache only
records tag names.
"""
import fnmatch
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_scan_catalogs, settings
from app.db.data_asset import DataAssetModel
from app.services.data_asset_owner import resolve_data_asset_owner
from app.services.tag_change.datasets import DatasetSummary, list_datasets
from app.workflows.tag_plan import CATALOG, SCHEMA, is_reserved_key, sql_string_literal

logger = logging.getLogger(__name__)

# Rows read from the cache per search. Beyond this the search is too broad to be
# useful; the response says it was truncated so the UI can ask for a narrower one.
MAX_SCAN = 5000
# Asset types in the cache that aren't Unity Catalog relations and can't carry tags.
_UNTAGGABLE_TYPES = ("DATA_PRODUCT",)
_SKIPPED_SCHEMAS = ("information_schema",)


@dataclass
class TagFilter:
    key: str
    value: Optional[str] = None  # None = any value; may contain * wildcards
    negate: bool = False

    def describe(self) -> str:
        if self.negate:
            return f"missing '{self.key}'"
        return f"{self.key}={self.value or '*'}"


@dataclass
class ParsedQuery:
    terms: List[str] = field(default_factory=list)  # lower-cased
    raw_terms: List[str] = field(default_factory=list)
    tag_filters: List[TagFilter] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.terms and not self.tag_filters


@dataclass
class SearchObject:
    fqn: str
    object_type: Optional[str] = None
    owner: Optional[str] = None
    tag_keys: List[str] = field(default_factory=list)
    in_cache: bool = True


@dataclass
class SearchResult:
    datasets: List[DatasetSummary]
    objects: List[SearchObject]
    total_objects: int
    truncated: bool
    filters: List[str]


@dataclass
class KeyUsage:
    key: str
    objects: List[SearchObject]  # catalogs, schemas, tables and views carrying the key
    columns: List[Tuple[str, str]]  # (table, column) pairs carrying it
    truncated: bool


def _kind_rank(obj: SearchObject) -> int:
    return {CATALOG: 0, SCHEMA: 1}.get(obj.object_type or "", 2)


def parse_query(q: str) -> ParsedQuery:
    parsed = ParsedQuery()
    for token in (q or "").split():
        if token.startswith("!") and len(token) > 1 and "=" not in token:
            parsed.tag_filters.append(TagFilter(key=token[1:], negate=True))
        elif "=" in token:
            key, value = token.split("=", 1)
            if key:
                parsed.tag_filters.append(TagFilter(key=key, value=None if value in ("", "*") else value))
        else:
            parsed.raw_terms.append(token)
            parsed.terms.append(token.lower())
    return parsed


def _matches_terms(name: str, terms: List[str]) -> bool:
    lowered = name.lower()
    for term in terms:
        if "*" in term or "?" in term:
            if not fnmatch.fnmatchcase(lowered, term):
                return False
        elif term not in lowered:
            return False
    return True


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if "*" in escaped:
        return escaped.replace("*", "%")
    return f"%{escaped}%"


def _catalog_clause(column: str) -> str:
    catalogs = get_scan_catalogs()
    if not catalogs:
        return f"{column} NOT IN ('system', 'samples')"
    listed = ", ".join(sql_string_literal(c) for c in catalogs)
    return f"{column} IN ({listed})"


def _name(parts: List[Any]) -> str:
    return ".".join(str(p) for p in parts if p not in (None, ""))


def _live_rows(provider, query: str) -> List[List[Any]]:
    try:
        resp = provider.client.statement_execution.execute_statement(
            statement=query,
            warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
            wait_timeout="30s",
        )
    except Exception as e:
        logger.warning(f"Tag search query failed: {e}")
        return []
    if resp.result and resp.result.data_array:
        return resp.result.data_array
    return []


def _tagged_with(provider, tag_filter: TagFilter) -> Set[str]:
    """Lower-cased names of catalogs, schemas, tables and views carrying ``tag_filter.key`` (and value)."""
    where = f"tag_name = {sql_string_literal(tag_filter.key)} AND {_catalog_clause('catalog_name')}"
    rows = _live_rows(
        provider,
        f"SELECT catalog_name, NULL, NULL, tag_value FROM system.information_schema.catalog_tags WHERE {where} "
        f"UNION ALL SELECT catalog_name, schema_name, NULL, tag_value "
        f"FROM system.information_schema.schema_tags WHERE {where} "
        f"UNION ALL SELECT catalog_name, schema_name, table_name, tag_value "
        f"FROM system.information_schema.table_tags WHERE {where}",
    )
    wanted = (tag_filter.value or "").lower()
    found: Set[str] = set()
    for row in rows:
        if len(row) < 4:
            continue
        value = "" if row[3] is None else str(row[3]).lower()
        if tag_filter.value is None or tag_filter.negate or fnmatch.fnmatchcase(value, wanted):
            found.add(_name(row[:3]).lower())
    return found


def key_usage(provider, key: str) -> KeyUsage:
    """Every catalog, schema, table, view and column in the governed catalogs tagged with ``key``."""
    where = (
        f"tag_name = {sql_string_literal(key)} AND {_catalog_clause('catalog_name')}"
    )
    not_info = "AND schema_name <> 'information_schema'"
    rows = _live_rows(
        provider,
        f"SELECT catalog_name, NULL, NULL, NULL FROM system.information_schema.catalog_tags WHERE {where} "
        f"UNION ALL SELECT catalog_name, schema_name, NULL, NULL "
        f"FROM system.information_schema.schema_tags WHERE {where} {not_info} "
        f"UNION ALL SELECT catalog_name, schema_name, table_name, NULL "
        f"FROM system.information_schema.table_tags WHERE {where} {not_info} "
        f"UNION ALL SELECT catalog_name, schema_name, table_name, column_name "
        f"FROM system.information_schema.column_tags WHERE {where} {not_info} "
        f"LIMIT {MAX_SCAN + 1}",
    )
    objects: Dict[str, SearchObject] = {}
    columns: Dict[str, Tuple[str, str]] = {}
    for row in rows[:MAX_SCAN]:
        if len(row) < 4 or not row[0]:
            continue
        if row[3]:
            table = _name(row[:3])
            columns[f"{table}::{row[3]}".lower()] = (table, str(row[3]))
            continue
        name = _name(row[:3])
        kind = CATALOG if not row[1] else SCHEMA if not row[2] else None
        objects.setdefault(name.lower(), SearchObject(fqn=name, object_type=kind, tag_keys=[key], in_cache=False))
    return KeyUsage(
        key=key,
        objects=sorted(objects.values(), key=lambda o: (_kind_rank(o), o.fqn.lower())),
        columns=sorted(columns.values(), key=lambda c: (c[0].lower(), c[1].lower())),
        truncated=len(rows) > MAX_SCAN,
    )


def _tag_names(tags: Any) -> List[str]:
    """The cache's tag-name list, which the sync sometimes stores JSON-encoded."""
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            return []
    if not isinstance(tags, list):
        return []
    out = []
    for t in tags:
        if isinstance(t, str):
            out.append(t.split("=", 1)[0].split(":", 1)[0].strip())
    return out


def _cache_candidates(db: Session, terms: List[str]) -> List[SearchObject]:
    query = db.query(
        DataAssetModel.id, DataAssetModel.type, DataAssetModel.owner, DataAssetModel.tags
    ).filter(
        ~DataAssetModel.type.in_(_UNTAGGABLE_TYPES),
        ~func.lower(DataAssetModel.schema).in_(_SKIPPED_SCHEMAS),
    )
    for term in terms:
        query = query.filter(func.lower(DataAssetModel.id).like(_like_pattern(term), escape="\\"))
    rows = query.order_by(DataAssetModel.id).limit(MAX_SCAN).all()
    results: List[SearchObject] = []
    for fqn, obj_type, owner, tags in rows:
        if fqn.count(".") != 2 or not _matches_terms(fqn, terms):
            continue
        keys = [t for t in _tag_names(tags) if not is_reserved_key(t)]
        results.append(SearchObject(fqn=fqn, object_type=obj_type, owner=resolve_data_asset_owner(owner, tags), tag_keys=sorted(keys)))
    return results


def _containers(pairs: List[Tuple[Any, Any]], terms: List[str]) -> List[SearchObject]:
    """Catalogs and schemas matching ``terms``, from ``(catalog, schema)`` pairs."""
    catalogs = sorted({str(c) for c, _ in pairs if c})
    schemas = sorted({f"{c}.{s}" for c, s in pairs if c and s and str(s).lower() not in _SKIPPED_SCHEMAS})
    return [SearchObject(fqn=c, object_type=CATALOG) for c in catalogs if _matches_terms(c, terms)] + [
        SearchObject(fqn=s, object_type=SCHEMA) for s in schemas if _matches_terms(s, terms)
    ]


def _cache_containers(db: Session, terms: List[str]) -> List[SearchObject]:
    pairs = (
        db.query(DataAssetModel.catalog, DataAssetModel.schema)
        .filter(~DataAssetModel.type.in_(_UNTAGGABLE_TYPES))
        .distinct()
        .all()
    )
    return _containers(pairs, terms)


def _like_clause(column_sql: str, terms: List[str]) -> List[str]:
    return [
        f"lower({column_sql}) LIKE {sql_string_literal(_like_pattern(term))} ESCAPE '\\\\'"
        for term in terms
    ]


def _live_candidates(provider, terms: List[str]) -> List[SearchObject]:
    """Fallback when the data-asset cache is empty (e.g. discovery sync is off)."""
    where = [
        _catalog_clause("table_catalog"),
        "table_schema <> 'information_schema'",
    ] + _like_clause("concat_ws('.', table_catalog, table_schema, table_name)", terms)
    rows = _live_rows(
        provider,
        "SELECT table_catalog, table_schema, table_name, table_type, table_owner "
        "FROM system.information_schema.tables WHERE "
        + " AND ".join(where)
        + f" ORDER BY 1, 2, 3 LIMIT {MAX_SCAN}",
    )
    schema_rows = _live_rows(
        provider,
        "SELECT catalog_name, schema_name FROM system.information_schema.schemata WHERE "
        + " AND ".join(
            [_catalog_clause("catalog_name"), "schema_name <> 'information_schema'"]
            + _like_clause("concat_ws('.', catalog_name, schema_name)", terms)
        )
        + f" ORDER BY 1, 2 LIMIT {MAX_SCAN}",
    )
    return _containers([(r[0], r[1]) for r in schema_rows if len(r) >= 2], terms) + [
        SearchObject(fqn=f"{r[0]}.{r[1]}.{r[2]}", object_type=r[3], owner=r[4])
        for r in rows
        if len(r) >= 5 and _matches_terms(f"{r[0]}.{r[1]}.{r[2]}", terms)
    ]


def search(db: Session, provider, q: str, limit: int = 200) -> SearchResult:
    parsed = parse_query(q)
    filters = [f.describe() for f in parsed.tag_filters]
    if parsed.is_empty:
        return SearchResult(datasets=[], objects=[], total_objects=0, truncated=False, filters=filters)

    datasets: List[DatasetSummary] = []
    if not parsed.tag_filters and parsed.terms:
        datasets = [d for d in list_datasets(db) if _matches_terms(d.dataset_id, parsed.terms)][:20]

    cached = _cache_candidates(db, parsed.terms)
    by_fqn: Dict[str, SearchObject] = {o.fqn.lower(): o for o in _cache_containers(db, parsed.terms) + cached}
    has_cache = bool(by_fqn) or db.query(DataAssetModel.id).first() is not None
    if not has_cache and provider is not None:
        by_fqn = {o.fqn.lower(): o for o in _live_candidates(provider, parsed.terms)}

    positive = [f for f in parsed.tag_filters if not f.negate]
    negative = [f for f in parsed.tag_filters if f.negate]
    if provider is not None and positive:
        # Start from what's tagged rather than from the cache, so a table tagged
        # since the last cache sync still shows up.
        matches: Optional[Set[str]] = None
        for tag_filter in positive:
            tagged = _tagged_with(provider, tag_filter)
            matches = tagged if matches is None else matches & tagged
        candidates = []
        for fqn in sorted(matches or set()):
            if not _matches_terms(fqn, parsed.terms):
                continue
            candidates.append(by_fqn.get(fqn) or SearchObject(fqn=fqn, in_cache=False))
    else:
        candidates = list(by_fqn.values())
    if provider is not None:
        for tag_filter in negative:
            tagged = _tagged_with(provider, tag_filter)
            candidates = [o for o in candidates if o.fqn.lower() not in tagged]

    if not candidates and len(parsed.raw_terms) == 1 and not parsed.tag_filters:
        # A pasted full name that isn't in the cache yet — offer it anyway; adding
        # it reads its live state and the plan flags it if it doesn't exist.
        term = parsed.raw_terms[0]
        if term.count(".") == 2 and "*" not in term and all(term.split(".")):
            candidates = [SearchObject(fqn=term, in_cache=False)]

    for obj in candidates:
        if not obj.object_type and obj.fqn.count(".") < 2:
            obj.object_type = CATALOG if "." not in obj.fqn else SCHEMA
    candidates.sort(key=_kind_rank)
    total = len(candidates)
    return SearchResult(
        datasets=datasets,
        objects=candidates[:limit],
        total_objects=total,
        truncated=total > limit or len(cached) >= MAX_SCAN,
        filters=filters,
    )
