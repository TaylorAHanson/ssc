"""
Tool to search the locally-cached data catalog (synced UC tables/views).

This scans the app's own ``data_assets`` table — populated periodically by the
data-asset sync — so it is fast (no live Databricks round trips) and is the
preferred FIRST step for "what data exists / where is X" discovery questions.
"""
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import String, case, cast, func, literal, or_

from app.tools.mcp import tool
from app.db.session import get_db
from app.db.data_asset import DataAssetModel
from app.db.legacy_dashboard import LegacyDashboardMappingModel
from app.services import catalog_search
from app.services.discover_domains import (
    EMPTY,
    OTHER_LABEL,
    DomainIndex,
    Membership,
    is_other,
    load_domain_index,
    membership_fields,
)
from app.services.discover_visibility import (
    DiscoverVisibility,
    hidden_linked_dashboards,
    load_visibility,
    visible_dashboards,
)


# A fully-qualified Unity Catalog name: catalog.schema.table (backticks optional).
_FQN = re.compile(r"(?<![\w.`-])`?([\w-]+)`?\.`?([\w-]+)`?\.`?([\w-]+)`?(?![\w.`-])")

# How many candidates are pulled from the table before the finer Python ranking. The pull is
# ordered by relevance first; without that, an arbitrary slice of a large catalog was ranked
# and the best match could be cut before it was ever scored.
_CANDIDATE_CAP = 200

_MAX_FAILED_RULES = 5


def _fully_qualified_name(query: str) -> Optional[str]:
    """The lowercase ``catalog.schema.table`` named in the query, if it names one."""
    m = _FQN.search(query or "")
    return ".".join(m.groups()).lower() if m else None


def _quality(asset: DataAssetModel) -> Optional[Dict[str, Any]]:
    """The cached data-quality summary, with a long failed-rule list cut to a few."""
    dq = asset.data_quality
    if not isinstance(dq, dict) or not dq:
        return None
    out = dict(dq)
    failed = out.get("failed_rules")
    if isinstance(failed, list) and len(failed) > _MAX_FAILED_RULES:
        out["failed_rules"] = failed[:_MAX_FAILED_RULES]
        out["failed_rules_total"] = len(failed)
    return out


def _serialize(asset: DataAssetModel, legacy: Optional[List[Dict[str, Any]]] = None,
               vis: Optional[DiscoverVisibility] = None,
               hidden_dashboards: Optional[set] = None,
               membership: Membership = EMPTY) -> Dict[str, Any]:
    vis = vis or DiscoverVisibility()
    return {
        "id": asset.id,
        "catalog": asset.catalog,
        "schema": asset.schema,
        "table_name": asset.table_name,
        "type": asset.type,
        "description": asset.description,
        "owner": asset.effective_owner,
        # Databricks Discover domains (tag keys); a subdomain's members are in its parent too.
        **membership_fields(membership),
        "kpis": asset.kpis or [],
        "downstream_dashboards": visible_dashboards(asset.downstream_dashboards or [], hidden_dashboards or set()),
        "upstream_tables": vis.visible_upstream(asset.upstream_tables or []),
        # Legacy (e.g. Tableau) dashboards this metric view replaces.
        "legacy_mappings": legacy or [],
        "tags": asset.tags or [],
        "certified": bool(asset.certified),
        "contract_url": asset.contract_url,
        # Service level and data-quality summary, when a policy scan or contract has set them.
        "sla": asset.sla,
        "data_quality": _quality(asset),
        # When this catalog entry was last refreshed from Unity Catalog (not when the data changed).
        "catalog_synced_at": asset.last_synced_at.isoformat() if asset.last_synced_at else None,
    }


def _memberships(db, index: DomainIndex) -> Dict[str, Membership]:
    """Domain membership of every cached asset that has one; empty when no domain is cached."""
    if not index:
        return {}
    rows = db.query(DataAssetModel.id, DataAssetModel.tags, DataAssetModel.scope_tag_keys).all()
    out = {asset_id: index.for_tags(tags, scope) for asset_id, tags, scope in rows}
    return {k: m for k, m in out.items() if m}


def _domain_scope(db, index: DomainIndex, memberships: Dict[str, Membership],
                  domain: Optional[str], subdomain: Optional[str]) -> Optional[set]:
    """Ids of assets in the named domain/subdomain, or None when neither is given.

    "Other" (unless a real domain is called that) means assets in no domain.
    """
    if not domain and not subdomain:
        return None
    if is_other(domain) or (domain and domain.strip().lower() == OTHER_LABEL.lower() and not index.get(domain)):
        if subdomain:
            return set()
        return {asset_id for (asset_id,) in db.query(DataAssetModel.id).all() if asset_id not in memberships}
    tops = {d.tag_key for d in index.search(domain) if not d.is_subdomain} if domain else None
    subs = None
    if subdomain:
        found = [d for d in index.search(subdomain) if d.is_subdomain]
        if tops is not None:
            found = [d for d in found if index.parent(d).tag_key in tops]
        subs = {d.tag_key for d in found}
    return {
        asset_id for asset_id, m in memberships.items()
        if (tops is None or tops.intersection(m.domains)) and (subs is None or subs.intersection(m.subdomains))
    }


def _membership_keys(m: Membership) -> List[str]:
    return [k.lower() for k in (*m.domains, *m.subdomains)]


def _domain_hits(memberships: Dict[str, Membership], token: str) -> list:
    """An ``id IN (...)`` clause for assets whose domain names contain ``token``, if any do."""
    ids = [i for i, m in memberships.items() if any(token in k for k in _membership_keys(m))]
    return [DataAssetModel.id.in_(ids)] if ids else []


class SearchDataAssetsInput(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        description=(
            "Keywords or asset name to search the local data catalog for "
            "(e.g. 'demand planning', 'forecast accuracy', 'sales orders', 'customer retention'). "
            "Matched against the table name, metric views, KPIs, description, "
            "owner, catalog, schema, Discover domain and subdomain names, and tags."
        ),
    )
    asset_type: Optional[str] = Field(
        default=None,
        description="Optional filter by asset type, e.g. 'METRIC_VIEW', 'VIEW', 'MANAGED', 'EXTERNAL', 'DATA_PRODUCT'.",
    )
    domain: Optional[str] = Field(
        default=None,
        description=(
            "Optional Databricks Discover domain filter: the domain's tag key or name, as returned in "
            "an asset's `domains` (e.g. 'supplychain'). Includes assets in its subdomains. "
            "'Other' finds assets that aren't in any domain."
        ),
    )
    subdomain: Optional[str] = Field(
        default=None,
        description=(
            "Optional Discover subdomain filter: its full tag key (e.g. 'supplychain/procurement') "
            "or its name within the domain (e.g. 'procurement')."
        ),
    )
    certified_only: bool = Field(
        default=False,
        description="If true, only return certified assets.",
    )
    limit: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum number of assets to return (ranked by relevance).",
    )


@tool(
    name="search_data_assets",
    description=(
        "Keyword search over the app's cached copy of the data catalog: the Metric Views, tables and views in the "
        "catalogs the app syncs on a schedule. Matches name, description, Discover domain and subdomain, KPIs, "
        "tags and owner, ranks governed Metric Views first, and returns each match's owner, certification, "
        "service level and data-quality summary. Use it FIRST for 'what data is there about X' or 'where is "
        "table Y'; it is instant and makes no Databricks call. The cache is one shared view for every user, not "
        "filtered to the user's grants, so it can't say whether this user has access. It returns tables, not a "
        "list of catalogs or schemas, and has no columns. A catalog outside the synced set, a catalog or schema "
        "with no tables, or anything created since the last sync is missing; use the live get_catalog_list, "
        "get_schema_list or get_table_list for those, and get_table_list with the exact name for columns."
    ),
    args_schema=SearchDataAssetsInput,
    feature_flag="data_discovery",
    friendly_label="Scanning the data catalog...",
    is_lookup=True,
)
def search_data_assets(
    query: str,
    asset_type: Optional[str] = None,
    domain: Optional[str] = None,
    subdomain: Optional[str] = None,
    certified_only: bool = False,
    limit: int = 15,
) -> Dict[str, Any]:
    """Keyword search over the cached ``data_assets`` table with relevance ranking."""
    db = next(get_db())
    try:
        search_cols = [
            DataAssetModel.id,
            DataAssetModel.table_name,
            DataAssetModel.description,
            DataAssetModel.owner,
            DataAssetModel.catalog,
            DataAssetModel.schema,
            # Tags are JSON (SQLite) / JSONB (Postgres); cast to text so a plain
            # ILIKE works the same on both — matches the tag NAMES we store.
            cast(DataAssetModel.tags, String),
            cast(DataAssetModel.kpis, String),
        ]

        q = db.query(DataAssetModel)
        vis = load_visibility(db)
        if vis.hidden_asset_ids:
            q = q.filter(DataAssetModel.id.notin_(vis.hidden_asset_ids))
        index = load_domain_index(db)
        memberships = _memberships(db, index)
        in_scope = _domain_scope(db, index, memberships, domain, subdomain)
        if in_scope is not None:
            q = q.filter(DataAssetModel.id.in_(in_scope))
        if asset_type:
            q = q.filter(DataAssetModel.type.ilike(f"%{asset_type}%"))
        if certified_only:
            q = q.filter(DataAssetModel.certified.is_(True))

        # One group per query word, holding its stem.
        groups = catalog_search.term_groups(query)
        tokens = [form for group in groups for form in group]
        if groups:
            # Match assets containing ANY word (OR), then rank by how many
            # words hit so e.g. "cancel pushout" surfaces a table named
            # ...cancel_pushout above tables matching only one word.
            token_clauses = [
                or_(*[col.ilike(catalog_search.like_pattern(form)) for col in search_cols for form in group],
                    *[hit for form in group for hit in _domain_hits(memberships, form)])
                for group in groups
            ]
            q = q.filter(or_(*token_clauses))
        else:
            # Whole query was stopwords/short — fall back to a literal contains.
            term = f"%{query.strip()}%"
            q = q.filter(or_(*[col.ilike(term) for col in search_cols]))

        # Order by relevance IN SQL before capping the pull. The catalog can hold tens of
        # thousands of assets and a many-word query matches thousands of them; capping an
        # unordered pull ranked an arbitrary slice and could drop the exact table asked for.
        # A fully-qualified name that matches an asset's id leads, then name hits, then the rest.
        relevance = literal(0)
        for tok in tokens:
            pat = catalog_search.like_pattern(tok)
            relevance = relevance + case(
                (or_(DataAssetModel.id.ilike(pat), DataAssetModel.table_name.ilike(pat)), 2), else_=0)
            relevance = relevance + case((or_(*[c.ilike(pat) for c in search_cols]), 1), else_=0)
        fqn = _fully_qualified_name(query)
        if fqn:
            relevance = relevance + case((func.lower(DataAssetModel.id) == fqn, 1000), else_=0)
        total_matched = q.order_by(None).count()
        candidates = q.order_by(relevance.desc(), DataAssetModel.id).limit(_CANDIDATE_CAP).all()

        # A user may only know the legacy (e.g. Tableau) dashboard name: pull in
        # the metric views mapped to any dashboard the query names.
        terms = tokens or [query.strip()]
        legacy_rows = [
            m for m in db.query(LegacyDashboardMappingModel).all() if not vis.is_hidden_id(m.metric_view_id)
        ]
        legacy_by_mv: Dict[str, List[Dict[str, Any]]] = {}
        for m in legacy_rows:
            legacy_by_mv.setdefault(m.metric_view_id, []).append(
                {"dashboard": m.dashboard, "status": m.status, "owner": m.owner})
        legacy_hits = {
            m.metric_view_id for m in legacy_rows
            if any(t.lower() in catalog_search.normalized(m.dashboard) for t in terms)
        }
        missing = legacy_hits - {a.id for a in candidates}
        if missing:
            extra = db.query(DataAssetModel).filter(DataAssetModel.id.in_(missing))
            if asset_type:
                extra = extra.filter(DataAssetModel.type.ilike(f"%{asset_type}%"))
            if in_scope is not None:
                extra = extra.filter(DataAssetModel.id.in_(in_scope))
            if certified_only:
                extra = extra.filter(DataAssetModel.certified.is_(True))
            candidates.extend(extra.all())
        candidates = vis.visible(candidates)

        def _score(asset: DataAssetModel) -> float:
            if not groups:
                return 1.0
            tag_text = " ".join(asset.tags) if isinstance(asset.tags, list) else ""
            kpi_text = ""
            if isinstance(asset.kpis, list):
                kpi_text = " ".join(f"{k.get('name', '')} {k.get('value', '')}" for k in asset.kpis if isinstance(k, dict))
            haystack = " ".join(
                v.lower()
                for v in (
                    asset.id,
                    asset.table_name,
                    asset.description,
                    asset.owner,
                    asset.effective_owner,
                    asset.catalog,
                    asset.schema,
                    " ".join(_membership_keys(memberships.get(asset.id, EMPTY))),
                    tag_text,
                    kpi_text,
                )
                if v
            )
            legacy_text = " ".join(d["dashboard"] for d in legacy_by_mv.get(asset.id, []))
            haystack = catalog_search.normalized(f"{haystack} {legacy_text}")
            name_hay = catalog_search.normalized(f"{asset.id or ''} {asset.table_name or ''}")
            score = sum(catalog_search.group_weight(group, haystack) for group in groups)
            # The exact table named in the query always leads.
            if fqn and (asset.id or "").lower() == fqn:
                score += 1000
            if not score:
                return 0.0
            # Boost matches that land in the name/FQN (more relevant than a
            # description mention) and give certified assets a slight edge.
            score += sum(catalog_search.group_weight(group, name_hay) for group in groups)
            if asset.certified:
                score += 0.5
            # Metric Views position over raw tables:
            is_metric = (
                str(asset.type).upper() == "METRIC_VIEW"
                or bool(asset.kpis)
                or (asset.table_name and asset.table_name.lower().startswith(("metric_", "sem_")))
            )
            if is_metric:
                score += 2.0
            return score

        scores = {id(a): _score(a) for a in candidates}
        # The SQL prefilter matches inside words; drop rows that only did so by coincidence.
        candidates = [a for a in candidates if scores[id(a)] > 0]
        candidates.sort(key=lambda a: scores[id(a)], reverse=True)
        top = candidates[:limit]
        hidden_dashboards = hidden_linked_dashboards(top)
        assets = [_serialize(a, legacy_by_mv.get(a.id), vis, hidden_dashboards, memberships.get(a.id, EMPTY))
                  for a in top]

        if assets:
            note = (
                "Cached Metric Views and UC tables/views from the local data catalog. "
                "Governed Metric Views are listed first with their KPIs, upstream tables, and downstream dashboards. "
                "Offer to summarize a metric view, launch a related dashboard, or request access."
            )
        else:
            note = (
                "No assets matched in the local catalog cache (it may be "
                "incomplete or the term isn't a table). Do NOT give up: try the "
                "live metadata tools (get_table_list / get_schema_list, after "
                "get_target_workspaces), or ask the user to narrow to a "
                "catalog/schema/business area."
            )

        return {
            "query": query,
            "count": len(assets),
            "total_matched": max(total_matched, len(candidates)),
            "assets": assets,
            "note": note,
        }
    finally:
        db.close()


class SearchMetricViewsInput(BaseModel):
    query: str = Field(
        default="",
        description="Search term for metric views, KPIs, or business questions (e.g. 'forecast accuracy', 'yield', 'order fulfillment').",
    )
    domain: Optional[str] = Field(
        default=None,
        description="Optional Databricks Discover domain filter: tag key or name (e.g. 'supplychain').",
    )
    subdomain: Optional[str] = Field(
        default=None,
        description="Optional Discover subdomain filter: full tag key or name within the domain (e.g. 'procurement').",
    )
    limit: int = Field(default=10, ge=1, le=30, description="Max metric views to return.")


@tool(
    name="search_metric_views",
    description=(
        "Search and discover governed Metric Views, business KPIs, and related dashboards/apps. "
        "Also finds the metric view that replaces a legacy (e.g. Tableau) dashboard by that dashboard's name. "
        "Use this as the preferred tool when the user asks about business metrics, KPIs, or domain reporting."
    ),
    args_schema=SearchMetricViewsInput,
    feature_flag="data_discovery",
    friendly_label="Finding governed metric views...",
)
def search_metric_views(
    query: str = "",
    domain: Optional[str] = None,
    subdomain: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """Search specifically for governed metric views with their KPIs and downstream dashboards."""
    return search_data_assets._func(
        query=query if query.strip() else "metric",
        asset_type="METRIC_VIEW",
        domain=domain,
        subdomain=subdomain,
        limit=limit,
    )
