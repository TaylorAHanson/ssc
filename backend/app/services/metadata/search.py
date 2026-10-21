"""Search, facets and key listing over desired + observed state (ADR-0006 D10). Workstream B.

Server-side paging, sorting and facets. Postgres uses ILIKE backed by the
``pg_trgm`` indexes (``db.migrate.apply_metadata_search_indexes``); SQLite uses
LIKE. Tag filters are EXISTS / NOT EXISTS over ``metadata_tags (key, value)``.

The searched set is a UNION ALL of two branches, each filtered on its own table so
its indexes apply:

* managed: active ``metadata_objects`` rows LEFT JOINed to their snapshot row;
* unmanaged (``include_unmanaged``): present ``metadata_observed`` rows with no
  active desired row. They have no desired state, so their ``tags`` and
  ``comment`` are the observed ones, tag filters match the snapshot's tags JSON,
  they are never drifted and have no ``object_state`` (an ``object_states``
  filter excludes them).

Count and facets run over a lean version of the union; only the page query
selects the display columns. Reads Lakebase only, never UC.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple, get_args

from sqlalchemy import and_, case, false, func, literal_column, not_, null, or_, select, union_all
from sqlalchemy.orm import Session

from app.db.metadata import OBJECT_ACTIVE, MetadataObjectModel, MetadataObservedModel, MetadataTagModel
from app.services.metadata import authz, environments, observed, policy
from app.services.metadata import scope as managed
from app.services.metadata.types import (
    COLUMN_SEPARATOR,
    RELATION_TYPES,
    Actor,
    DatasetSummary,
    Facet,
    KeyInfo,
    ObjectSummary,
    SearchQuery,
    SearchResult,
    SecurableType,
    TagFilter,
    normalize_value,
)

logger = logging.getLogger(__name__)

_O = MetadataObjectModel.__table__
_T = MetadataTagModel.__table__
_B = MetadataObservedModel.__table__

NON_COLUMN_TYPES = tuple(t for t in get_args(SecurableType) if t != "column")
DATASET_KEYS = ("dataset", "data_set")
TAG_KEY_FACET_LIMIT = 50
FACET_LIMIT = 500
KEY_VALUE_LIMIT = 10
_ESCAPE = "\\"
_DATE_SORTS = ("updated_at", "last_applied_at", "drift_detected_at")


@dataclass
class _Ctx:
    query: SearchQuery
    dialect: str
    scope: "authz.EditableScope"
    # Snapshot catalog -> environment, for unmanaged rows (empty when they're excluded).
    env_by_catalog: Dict[str, Optional[str]]
    unmanaged: bool
    # Catalogs inside the scope's environments; None when the scope has no environment limit.
    scope_catalogs: Optional[List[str]] = None


# --- Pattern helpers ----------------------------------------------------------------


def _escape_like(text: str) -> str:
    return text.replace(_ESCAPE, _ESCAPE * 2).replace("%", _ESCAPE + "%").replace("_", _ESCAPE + "_")


def _glob_parts(glob: str) -> Optional[List[str]]:
    """A name glob's dot-separated parts (lowercased), or None when it can't match anything."""
    parts = glob.strip().lower().split(".")
    return parts if all(parts) and len(parts) <= 3 else None


def _glob_like(part: str) -> str:
    """fnmatch-style ``*`` / ``?`` as LIKE ``%`` / ``_``; everything else literal."""
    out = []
    for ch in part:
        if ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        elif ch in ("%", "_", _ESCAPE):
            out.append(_ESCAPE + ch)
        else:
            out.append(ch)
    return "".join(out)


def _glob_regex(part: str) -> "re.Pattern[str]":
    return re.compile("".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in part), re.DOTALL)


def _ci_like(column, pattern: str, dialect: str):
    """Case-insensitive LIKE: ILIKE on Postgres (trigram-indexed), LIKE on SQLite (ASCII case-insensitive)."""
    if dialect == "sqlite":
        return column.like(pattern, escape=_ESCAPE)
    return column.ilike(pattern, escape=_ESCAPE)


def _norm_keys(keys: Sequence[str]) -> List[str]:
    return sorted({str(k).strip().lower() for k in keys or [] if str(k).strip()})


def _is_under(fqn_key: str, ancestor: str) -> bool:
    """``fqn_key`` is ``ancestor`` or nested below it (``.child`` or ``::column``)."""
    return (
        fqn_key == ancestor
        or fqn_key.startswith(ancestor + ".")
        or fqn_key.startswith(ancestor + COLUMN_SEPARATOR)
    )


def _under_clause(fqn, ancestor: str):
    esc = _escape_like(ancestor)
    return or_(
        fqn == ancestor,
        fqn.like(esc + ".%", escape=_ESCAPE),
        fqn.like(esc + COLUMN_SEPARATOR + "%", escape=_ESCAPE),
    )


def _glob_clause(name_parts: Sequence, parts: List[str]):
    """The object's name (catalog, schema, relation; a column goes by its relation) or an
    ancestor's matches the glob part by part, so ``*`` never spans a dot."""
    return and_(*[
        func.lower(column).like(_glob_like(part), escape=_ESCAPE) for column, part in zip(name_parts, parts)
    ])


def _glob_matches(fqn_key: str, patterns: List["re.Pattern[str]"]) -> bool:
    names = fqn_key.partition(COLUMN_SEPARATOR)[0].split(".")
    return len(patterns) <= len(names) and all(rx.fullmatch(n) for n, rx in zip(names, patterns))


# --- Editable scope -----------------------------------------------------------------


def _owned_terms(scope) -> List[tuple]:
    """(owned key, not-owned keys nested under it) — the nearest owner tag wins."""
    owned, not_owned = _norm_keys(scope.owned), _norm_keys(scope.not_owned)
    return [(o, [n for n in not_owned if n != o and _is_under(n, o)]) for o in owned]


def _scope_globs(scope) -> List[List[str]]:
    return [parts for parts in (_glob_parts(g) for g in _norm_keys(scope.globs)) if parts]


def _editable_clause(fqn, name_parts: Sequence, scope):
    terms = []
    for owned, overridden in _owned_terms(scope):
        term = _under_clause(fqn, owned)
        if overridden:
            term = and_(term, not_(or_(*[_under_clause(fqn, n) for n in overridden])))
        terms.append(term)
    terms += [_glob_clause(name_parts, parts) for parts in _scope_globs(scope)]
    return or_(*terms) if terms else false()


def editable_predicate(editable_scope) -> Callable[[str], bool]:
    """The same rule as the SQL filter, for one fqn_key."""
    if editable_scope.all:
        return lambda key: managed.manages(managed.catalog_of(key))
    owned = _owned_terms(editable_scope)
    globs = [[_glob_regex(p) for p in parts] for parts in _scope_globs(editable_scope)]
    envs = set(_norm_keys(getattr(editable_scope, "environments", None) or []))

    def editable(fqn_key: str) -> bool:
        key = fqn_key.lower()
        if not managed.manages(managed.catalog_of(key)):
            return False
        if envs and (environments.environment_of(key.split(".", 1)[0]) or "").lower() not in envs:
            return False
        for o, overridden in owned:
            if _is_under(key, o) and not any(_is_under(key, n) for n in overridden):
                return True
        return any(_glob_matches(key, patterns) for patterns in globs)

    return editable


# --- Filters ------------------------------------------------------------------------


def _common_where(ctx: _Ctx, *, fqn, catalog_key, name_parts: Sequence, stype, comments: Sequence) -> List:
    """``name_parts``: the branch's (catalog, schema_name, object_name) columns."""
    q = ctx.query
    where: List = []
    term = (q.q or "").strip()
    if term:
        pattern = "%" + _escape_like(term) + "%"
        where.append(or_(
            fqn.like("%" + _escape_like(term.lower()) + "%", escape=_ESCAPE),
            *[_ci_like(c, pattern, ctx.dialect) for c in comments],
        ))
    catalogs = _norm_keys(q.catalogs)
    if catalogs:
        where.append(catalog_key.in_(catalogs))
    if q.schemas:
        schema = func.lower(name_parts[1])
        clauses = []
        for entry in _norm_keys(q.schemas):
            cat, dot, name = entry.rpartition(".")
            clauses.append(and_(catalog_key == cat, schema == name) if dot else schema == name)
        where.append(or_(*clauses))
    if q.securable_types:
        where.append(stype.in_(list(dict.fromkeys(q.securable_types))))
    elif not q.include_columns:
        where.append(stype.in_(NON_COLUMN_TYPES))
    if q.editable_only and not ctx.scope.all:
        where.append(_editable_clause(fqn, name_parts, ctx.scope))
        if ctx.scope_catalogs is not None:
            where.append(catalog_key.in_(ctx.scope_catalogs))
    if q.editable_only:
        catalogs_managed = managed.patterns()
        if catalogs_managed and "*" not in catalogs_managed:
            where.append(or_(*[_glob_clause(name_parts[:1], [p]) for p in catalogs_managed]))
    return where


def _tag_filters(query: SearchQuery) -> List[TagFilter]:
    return [f for f in query.tags if (f.key or "").strip()]


def _dataset_filters(query: SearchQuery) -> List[TagFilter]:
    """Either spelling of the dataset key with the query's dataset; none without one."""
    name = (query.dataset or "").strip()
    return [TagFilter(key=k, value=name) for k in DATASET_KEYS] if name else []


def _desired_tag_clause(f: TagFilter):
    conditions = [_T.c.object_id == _O.c.id, _T.c.key == f.key.strip()]
    if f.op == "eq":
        value = normalize_value(f.value)
        conditions.append(_T.c.value.is_(None) if value is None else _T.c.value == value)
    found = select(_T.c.id).where(*conditions).correlate(_O).exists()
    return ~found if f.op == "missing" else found


def _observed_tag_clause(f: TagFilter, dialect: str):
    key = f.key.strip()
    value = normalize_value(f.value) if f.op == "eq" else None
    tags = _B.c.tags
    if dialect == "postgresql":
        node = tags[key]  # json -> key: SQL NULL when absent, JSON null for a key-only tag
        if f.op == "exists":
            return node.isnot(None)
        if f.op == "missing":
            return node.is_(None)
        if value is None:
            return func.json_typeof(node) == "null"
        return tags[key].as_string() == value
    if '"' in key or _ESCAPE in key:
        # A JSON path can't quote these; walk the object instead.
        each = func.json_each(tags).table_valued("key", "value", "type")
        conditions = [each.c.key == key]
        if f.op == "eq":
            conditions.append(each.c.type == "null" if value is None else each.c.value == value)
        found = select(literal_column("1")).select_from(each).where(*conditions).correlate(_B).exists()
        return ~found if f.op == "missing" else found
    path = f'$."{key}"'
    kind = func.json_type(tags, path)
    if f.op == "exists":
        return kind.isnot(None)
    if f.op == "missing":
        return kind.is_(None)
    if value is None:
        return kind == "null"
    return and_(kind == "text", func.json_extract(tags, path) == value)


def _managed_where(ctx: _Ctx) -> List:
    q = ctx.query
    where = [_O.c.status == OBJECT_ACTIVE]
    where += _common_where(
        ctx, fqn=_O.c.fqn_key, catalog_key=func.lower(_O.c.catalog),
        name_parts=(_O.c.catalog, _O.c.schema_name, _O.c.object_name),
        stype=_O.c.securable_type, comments=[_O.c.comment, _B.c.comment],
    )
    if q.environments:
        where.append(_O.c.environment.in_(list(q.environments)))
    if q.object_states:
        where.append(_O.c.object_state.in_(list(q.object_states)))
    where += [_desired_tag_clause(f) for f in _tag_filters(q)]
    if _dataset_filters(q):
        where.append(or_(*[_desired_tag_clause(f) for f in _dataset_filters(q)]))
    if q.drift is True:
        where.append(_O.c.drift_detected_at.isnot(None))
    elif q.drift is False:
        where.append(_O.c.drift_detected_at.is_(None))
    return where


def _unmanaged_where(ctx: _Ctx) -> List:
    q = ctx.query
    desired = (
        select(_O.c.id).where(_O.c.fqn_key == _B.c.fqn_key, _O.c.status == OBJECT_ACTIVE).correlate(_B).exists()
    )
    where = [_B.c.present.is_(True), ~desired]
    where += _common_where(
        ctx, fqn=_B.c.fqn_key, catalog_key=_B.c.catalog,
        name_parts=(_B.c.catalog, _B.c.schema_name, _B.c.object_name),
        stype=_B.c.securable_type, comments=[_B.c.comment],
    )
    if q.environments:
        wanted = set(q.environments)
        catalogs = sorted(c for c, env in ctx.env_by_catalog.items() if env in wanted)
        where.append(_B.c.catalog.in_(catalogs) if catalogs else false())
    where += [_observed_tag_clause(f, ctx.dialect) for f in _tag_filters(q)]
    if _dataset_filters(q):
        where.append(or_(*[_observed_tag_clause(f, ctx.dialect) for f in _dataset_filters(q)]))
    return where


# --- The searched set ---------------------------------------------------------------


def _unmanaged_environment(ctx: _Ctx):
    mapping = {c: env for c, env in ctx.env_by_catalog.items() if env}
    return case(mapping, value=_B.c.catalog, else_=null()) if mapping else null()


def _union(ctx: _Ctx, *, full: bool):
    """The filtered set as one subquery; ``full`` adds the display columns."""
    managed_cols = [
        _O.c.id.label("id"),
        _O.c.fqn_key.label("fqn_key"),
        func.lower(_O.c.catalog).label("catalog_key"),
        func.lower(_O.c.schema_name).label("schema_key"),
        _O.c.securable_type.label("securable_type"),
        _O.c.environment.label("environment"),
        _O.c.object_state.label("object_state"),
        literal_column("1").label("managed"),
    ]
    if full:
        managed_cols += [
            _O.c.catalog.label("catalog"),
            _O.c.schema_name.label("schema_name"),
            _O.c.object_name.label("object_name"),
            _O.c.column_name.label("column_name"),
            _O.c.comment.label("comment"),
            _O.c.comment_version.label("comment_version"),
            _B.c.comment.label("observed_comment"),
            _B.c.tags.label("observed_tags"),
            _B.c.present.label("observed_present"),
            _B.c.data_type.label("data_type"),
            _O.c.drift_detected_at.label("drift_detected_at"),
            _O.c.last_apply_status.label("last_apply_status"),
            _O.c.last_applied_at.label("last_applied_at"),
            _O.c.updated_at.label("updated_at"),
        ]
    # The snapshot is needed for display columns and for q (observed comments).
    joined = full or bool((ctx.query.q or "").strip())
    source = _O.outerjoin(_B, _B.c.fqn_key == _O.c.fqn_key) if joined else _O
    branches = [select(*managed_cols).select_from(source).where(*_managed_where(ctx))]

    if ctx.unmanaged:
        unmanaged_cols = [
            null().label("id"),
            _B.c.fqn_key.label("fqn_key"),
            _B.c.catalog.label("catalog_key"),
            func.lower(_B.c.schema_name).label("schema_key"),
            _B.c.securable_type.label("securable_type"),
            _unmanaged_environment(ctx).label("environment"),
            null().label("object_state"),
            literal_column("0").label("managed"),
        ]
        if full:
            unmanaged_cols += [
                _B.c.catalog.label("catalog"),
                _B.c.schema_name.label("schema_name"),
                _B.c.object_name.label("object_name"),
                _B.c.column_name.label("column_name"),
                _B.c.comment.label("comment"),
                null().label("comment_version"),
                _B.c.comment.label("observed_comment"),
                _B.c.tags.label("observed_tags"),
                _B.c.present.label("observed_present"),
                _B.c.data_type.label("data_type"),
                null().label("drift_detected_at"),
                null().label("last_apply_status"),
                null().label("last_applied_at"),
                null().label("updated_at"),
            ]
        branches.append(select(*unmanaged_cols).select_from(_B).where(*_unmanaged_where(ctx)))

    stmt = branches[0] if len(branches) == 1 else union_all(*branches)
    return stmt.subquery("found")


def _order_by(found, query: SearchQuery) -> List:
    def directed(column):
        return column.desc() if query.order == "desc" else column.asc()

    if query.sort == "name":
        return [directed(found.c.fqn_key)]
    if query.sort in _DATE_SORTS:
        return [directed(found.c[query.sort]).nulls_last(), found.c.fqn_key.asc()]
    if query.sort == "catalog":
        return [directed(found.c.catalog_key), found.c.fqn_key.asc()]
    return [directed(found.c.securable_type), found.c.fqn_key.asc()]


def _facet(db: Session, column, limit: int = FACET_LIMIT) -> List[Facet]:
    count = func.count()
    rows = db.execute(
        select(column, count).where(column.isnot(None)).group_by(column).order_by(count.desc(), column).limit(limit)
    ).all()
    return [Facet(value=str(value), count=int(n)) for value, n in rows]


def _tag_key_facet(db: Session, ctx: _Ctx) -> List[Facet]:
    """Top tag keys over the managed rows in the filtered set."""
    source = _O.outerjoin(_B, _B.c.fqn_key == _O.c.fqn_key) if (ctx.query.q or "").strip() else _O
    ids = select(_O.c.id).select_from(source).where(*_managed_where(ctx))
    count = func.count()
    rows = db.execute(
        select(_T.c.key, count).where(_T.c.object_id.in_(ids))
        .group_by(_T.c.key).order_by(count.desc(), _T.c.key).limit(TAG_KEY_FACET_LIMIT)
    ).all()
    return [Facet(value=str(key), count=int(n)) for key, n in rows]


def _snapshot_environments(db: Session) -> Dict[str, Optional[str]]:
    catalogs = db.execute(select(_B.c.catalog).distinct()).scalars().all()
    return {c: environments.environment_of(c) for c in catalogs if c}


def _scope_catalogs(db: Session, scope) -> Optional[List[str]]:
    """Known catalogs in the scope's environments, or None when it has no environment limit."""
    envs = set(_norm_keys(getattr(scope, "environments", None) or []))
    if scope.all or not envs:
        return None
    known = set(db.execute(select(func.lower(_B.c.catalog)).distinct()).scalars().all())
    known |= set(db.execute(select(func.lower(_O.c.catalog)).distinct()).scalars().all())
    return sorted(c for c in known if c and (environments.environment_of(c) or "").lower() in envs)


def _json_tags(value) -> Optional[Dict[str, Optional[str]]]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return {str(k): normalize_value(v) for k, v in dict(value).items()}


def _page_tags(db: Session, ids: List[str]) -> Tuple[Dict[str, Dict[str, Optional[str]]], Dict[str, Dict[str, int]]]:
    """Each object's desired tags and their versions, keyed by object id."""
    tags: Dict[str, Dict[str, Optional[str]]] = {}
    versions: Dict[str, Dict[str, int]] = {}
    if not ids:
        return tags, versions
    rows = db.execute(
        select(_T.c.object_id, _T.c.key, _T.c.value, _T.c.version)
        .where(_T.c.object_id.in_(ids)).order_by(_T.c.object_id, _T.c.key)
    )
    for object_id, key, value, version in rows:
        tags.setdefault(object_id, {})[key] = value
        versions.setdefault(object_id, {})[key] = int(version or 0)
    return tags, versions


def _summary(
    row,
    tags: Dict[str, Dict[str, Optional[str]]],
    versions: Dict[str, Dict[str, int]],
    editable: Callable[[str], bool],
) -> ObjectSummary:
    managed = bool(row["managed"])
    observed_present = bool(row["observed_present"])
    observed_tags = _json_tags(row["observed_tags"]) if observed_present else None
    observed_comment = row["observed_comment"] if observed_present else None
    name = ".".join(p for p in (row["catalog"], row["schema_name"], row["object_name"]) if p)
    return ObjectSummary(
        id=row["id"],
        fqn_key=row["fqn_key"],
        name=name,
        column=row["column_name"],
        securable_type=row["securable_type"],
        catalog=row["catalog"],
        schema_name=row["schema_name"],
        object_name=row["object_name"],
        environment=row["environment"],
        managed=managed,
        tags=tags.get(row["id"], {}) if managed else dict(observed_tags or {}),
        tag_versions=versions.get(row["id"], {}) if managed else None,
        comment=row["comment"],
        comment_version=int(row["comment_version"] or 0) if managed else None,
        observed_tags=observed_tags,
        observed_comment=observed_comment,
        object_state=row["object_state"],
        drift=row["drift_detected_at"] is not None,
        drift_detected_at=row["drift_detected_at"],
        last_apply_status=row["last_apply_status"],
        last_applied_at=row["last_applied_at"],
        updated_at=row["updated_at"],
        data_type=row["data_type"],
        editable=editable(row["fqn_key"]),
    )


def search(db: Session, query: SearchQuery, actor: Actor) -> SearchResult:
    """One page of objects matching ``query``, with the total and facets over the whole filtered set.

    * ``q``: case-insensitive substring of the fqn_key or the comment (desired or observed).
    * ``catalogs``: catalog names; ``schemas``: ``catalog.schema`` or a bare schema name.
    * ``securable_types``: listing ``column`` includes columns even without ``include_columns``.
    * ``tags``: exact key and value; ``key=`` (value None) matches key-only tags.
    * ``dataset``: tagged ``dataset`` or ``data_set`` with exactly this value.
    * ``editable_only``: ``authz.editable_scope`` — the nearest owner-tagged object at or
      above is owned, or a principal glob covers the name (or an ancestor's) part by
      part: ``*`` and ``?`` within one part; a column goes by its relation.
    * Facets: catalog (lowercased), securable_type, environment, object_state and the
      top 50 tag keys of the managed rows; NULLs aren't counted. With exactly one catalog
      in ``catalogs``, also schema (lowercased bare name).
    """
    started = time.perf_counter()
    dialect = db.get_bind().dialect.name
    scope = authz.editable_scope(db, actor)
    unmanaged = bool(query.include_unmanaged) and query.drift is not True and not query.object_states
    ctx = _Ctx(
        query=query,
        dialect=dialect,
        scope=scope,
        env_by_catalog=_snapshot_environments(db) if unmanaged else {},
        unmanaged=unmanaged,
        scope_catalogs=_scope_catalogs(db, scope) if query.editable_only else None,
    )

    lean = _union(ctx, full=False)
    total = int(db.execute(select(func.count()).select_from(lean)).scalar() or 0)

    items: List[ObjectSummary] = []
    facets: Dict[str, List[Facet]] = {
        "catalog": [], "securable_type": [], "environment": [], "object_state": [], "tag_key": [],
    }
    if total:
        found = _union(ctx, full=True)
        rows = db.execute(
            select(found).order_by(*_order_by(found, query))
            .offset((query.page - 1) * query.page_size).limit(query.page_size)
        ).mappings().all()
        tags, versions = _page_tags(db, [r["id"] for r in rows if r["managed"] and r["id"]])
        editable = editable_predicate(scope)
        items = [_summary(r, tags, versions, editable) for r in rows]
        facets = {
            "catalog": _facet(db, lean.c.catalog_key),
            "securable_type": _facet(db, lean.c.securable_type),
            "environment": _facet(db, lean.c.environment),
            "object_state": _facet(db, lean.c.object_state),
            "tag_key": _tag_key_facet(db, ctx),
        }
        if len(query.catalogs or []) == 1:
            facets["schema"] = _facet(db, lean.c.schema_key)

    took_ms = int((time.perf_counter() - started) * 1000)
    logger.debug("Metadata search: %d of %d object(s) in %d ms.", len(items), total, took_ms)
    return SearchResult(
        items=items, total=total, page=query.page, page_size=query.page_size, facets=facets, took_ms=took_ms,
    )


# --- Keys ---------------------------------------------------------------------------


def list_keys(db: Session, *, q: str = "", limit: int = 50) -> List[KeyInfo]:
    """Known tag keys with usage counts and top values, merged with key policies.

    ``count`` is the number of active desired objects carrying the key; ``values``
    are its 10 most used values, with a key-only tag (no value) shown as ``""``.
    Keys that only have a policy appear with count 0. ``q`` is a case-insensitive
    substring of the key. Sorted by count, then key; at most ``limit`` entries.
    """
    limit = max(1, int(limit or 50))
    term = (q or "").strip()
    dialect = db.get_bind().dialect.name
    active = _T.join(_O, and_(_O.c.id == _T.c.object_id, _O.c.status == OBJECT_ACTIVE))
    count = func.count()

    used = select(_T.c.key, count).select_from(active)
    if term:
        used = used.where(_ci_like(_T.c.key, "%" + _escape_like(term) + "%", dialect))
    counts: Dict[str, int] = {
        str(key): int(n)
        for key, n in db.execute(used.group_by(_T.c.key).order_by(count.desc(), _T.c.key).limit(limit)).all()
    }

    values: Dict[str, List[Facet]] = {}
    if counts:
        rows = db.execute(
            select(_T.c.key, _T.c.value, count).select_from(active)
            .where(_T.c.key.in_(list(counts))).group_by(_T.c.key, _T.c.value)
        ).all()
        by_key: Dict[str, List[tuple]] = {}
        for key, value, n in rows:
            by_key.setdefault(str(key), []).append(("" if value is None else str(value), int(n)))
        for key, pairs in by_key.items():
            pairs.sort(key=lambda p: (-p[1], p[0]))
            values[key] = [Facet(value=v, count=n) for v, n in pairs[:KEY_VALUE_LIMIT]]

    policies = {p.key: p for p in policy.key_policies()}
    policies_lower = {k.lower(): p for k, p in policies.items()}
    governed = observed.governed_tags(db)
    governed_lower = {k.lower() for k in governed}

    keys = list(counts)
    needle = term.lower()
    for key in policies:
        if key not in counts and (not needle or needle in key.lower()):
            keys.append(key)

    infos = [
        KeyInfo(
            key=key,
            count=counts.get(key, 0),
            values=values.get(key, []),
            policy=policies.get(key) or policies_lower.get(key.lower()),
            exempt=bool(policy.is_exempt(key)),
            governed=key in governed or key.lower() in governed_lower,
        )
        for key in keys
    ]
    infos.sort(key=lambda info: (-info.count, info.key.lower()))
    return infos[:limit]


# --- Datasets -----------------------------------------------------------------------


def list_datasets(db: Session) -> List[DatasetSummary]:
    """Every value of a table's or view's ``dataset`` / ``data_set`` tag, sorted by name.

    Tags are read as search reads them: desired for managed objects, the snapshot's
    otherwise. A relation tagged with both spellings is counted once per value.
    """
    dialect = db.get_bind().dialect.name
    tables: Dict[str, set] = {}
    catalogs: Dict[str, set] = {}
    drifted: Dict[str, set] = {}

    def add(value, fqn_key: str, catalog: str, drift: bool) -> None:
        name = normalize_value(value)
        if not name:
            return
        tables.setdefault(name, set()).add(fqn_key)
        catalogs.setdefault(name, set()).add(catalog.lower())
        if drift:
            drifted.setdefault(name, set()).add(fqn_key)

    for fqn_key, catalog, drift_at, value in db.execute(
        select(_O.c.fqn_key, _O.c.catalog, _O.c.drift_detected_at, _T.c.value)
        .select_from(_T.join(_O, _O.c.id == _T.c.object_id))
        .where(_O.c.status == OBJECT_ACTIVE, _O.c.securable_type.in_(RELATION_TYPES), _T.c.key.in_(DATASET_KEYS))
    ):
        add(value, fqn_key, catalog, drift_at is not None)

    desired = select(_O.c.id).where(_O.c.fqn_key == _B.c.fqn_key, _O.c.status == OBJECT_ACTIVE).correlate(_B).exists()
    tagged = or_(*[_observed_tag_clause(TagFilter(key=k, op="exists"), dialect) for k in DATASET_KEYS])
    for fqn_key, catalog, raw in db.execute(
        select(_B.c.fqn_key, _B.c.catalog, _B.c.tags)
        .where(_B.c.present.is_(True), ~desired, _B.c.securable_type.in_(RELATION_TYPES), tagged)
    ):
        tags = _json_tags(raw) or {}
        for key in DATASET_KEYS:
            add(tags.get(key), fqn_key, catalog, False)

    return [
        DatasetSummary(
            name=name, table_count=len(keys), catalogs=sorted(catalogs[name]),
            out_of_sync_count=len(drifted.get(name, ())),
        )
        for name, keys in sorted(tables.items(), key=lambda item: item[0].lower())
    ]
