"""
Tool to search the locally-cached data catalog (synced UC tables/views).

This scans the app's own ``data_assets`` table — populated periodically by the
data-asset sync — so it is fast (no live Databricks round trips) and is the
preferred FIRST step for "what data exists / where is X" discovery questions.
"""
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import String, cast, or_

from app.tools.mcp import tool
from app.db.session import get_db
from app.db.data_asset import DataAssetModel


# Common filler words that would only add noise to a keyword scan.
_STOPWORDS = {
    "data", "table", "tables", "dataset", "datasets", "the", "a", "an", "info",
    "information", "about", "show", "me", "list", "find", "get", "all", "for",
    "of", "in", "on", "any", "what", "which", "is", "are", "do", "we", "have",
}


def _tokenize(query: str) -> List[str]:
    """Split a free-text query into meaningful lowercase keywords."""
    raw = re.split(r"[^a-zA-Z0-9]+", query.lower())
    return [t for t in raw if len(t) >= 2 and t not in _STOPWORDS]


def _serialize(asset: DataAssetModel) -> Dict[str, Any]:
    return {
        "id": asset.id,
        "catalog": asset.catalog,
        "schema": asset.schema,
        "table_name": asset.table_name,
        "type": asset.type,
        "description": asset.description,
        "owner": asset.owner,
        "domain": asset.domain,
        "subdomain": asset.subdomain,
        "kpis": asset.kpis or [],
        "downstream_dashboards": asset.downstream_dashboards or [],
        "upstream_tables": asset.upstream_tables or [],
        "legacy_mappings": asset.legacy_mappings or [],
        "tags": asset.tags or [],
        "certified": bool(asset.certified),
        "contract_url": asset.contract_url,
    }


class SearchDataAssetsInput(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        description=(
            "Keywords or asset name to search the local data catalog for "
            "(e.g. 'demand planning', 'forecast accuracy', 'sales orders', 'customer retention'). "
            "Matched against the table name, metric views, KPIs, description, "
            "owner, catalog, schema, domain, subdomain, and tags."
        ),
    )
    asset_type: Optional[str] = Field(
        default=None,
        description="Optional filter by asset type, e.g. 'METRIC_VIEW', 'VIEW', 'MANAGED', 'EXTERNAL', 'DATA_PRODUCT'.",
    )
    domain: Optional[str] = Field(
        default=None,
        description="Optional domain filter (e.g. 'Supply Chain', 'Finance', 'Sales & Commercial').",
    )
    subdomain: Optional[str] = Field(
        default=None,
        description="Optional subdomain filter (e.g. 'Planning & Forecasting', 'Order Management').",
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
        "Search cached Unity Catalog data assets (Metric Views, tables, views, schemas) by keyword across name, "
        "description, domain, subdomain, KPIs, tags, and owner. Positions governed Metric Views over raw tables. "
        "Fast local lookup for data discovery without live Databricks API calls."
    ),
    args_schema=SearchDataAssetsInput,
    feature_flag="data_discovery",
    friendly_label="Scanning the data catalog...",
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
            DataAssetModel.domain,
            DataAssetModel.subdomain,
            # Tags are JSON (SQLite) / JSONB (Postgres); cast to text so a plain
            # ILIKE works the same on both — matches the tag NAMES we store.
            cast(DataAssetModel.tags, String),
            cast(DataAssetModel.kpis, String),
        ]

        q = db.query(DataAssetModel)
        if asset_type:
            q = q.filter(DataAssetModel.type.ilike(f"%{asset_type}%"))
        if domain:
            q = q.filter(DataAssetModel.domain.ilike(f"%{domain}%"))
        if subdomain:
            q = q.filter(DataAssetModel.subdomain.ilike(f"%{subdomain}%"))
        if certified_only:
            q = q.filter(DataAssetModel.certified.is_(True))

        tokens = _tokenize(query)
        if tokens:
            # Match assets containing ANY token (OR), then rank by how many
            # tokens hit so e.g. "cancel pushout" surfaces a table named
            # ...cancel_pushout above tables matching only one word.
            token_clauses = [
                or_(*[col.ilike(f"%{tok}%") for col in search_cols]) for tok in tokens
            ]
            q = q.filter(or_(*token_clauses))
        else:
            # Whole query was stopwords/short — fall back to a literal contains.
            term = f"%{query.strip()}%"
            q = q.filter(or_(*[col.ilike(term) for col in search_cols]))

        # Cap the candidate pull; the local table is small and we rank in Python.
        candidates = q.limit(200).all()

        def _score(asset: DataAssetModel) -> float:
            if not tokens:
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
                    asset.catalog,
                    asset.schema,
                    asset.domain,
                    asset.subdomain,
                    tag_text,
                    kpi_text,
                )
                if v
            )
            name_hay = f"{asset.id or ''} {asset.table_name or ''}".lower()
            score = sum(1 for t in tokens if t in haystack)
            # Boost matches that land in the name/FQN (more relevant than a
            # description mention) and give certified assets a slight edge.
            score += sum(1 for t in tokens if t in name_hay)
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

        candidates.sort(key=_score, reverse=True)
        top = candidates[:limit]
        assets = [_serialize(a) for a in top]

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
                "get_target_workspaces), offer ask_your_data (Genie), or ask the "
                "user to narrow to a catalog/schema/business area."
            )

        return {
            "query": query,
            "count": len(assets),
            "total_matched": len(candidates),
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
        description="Optional domain filter (e.g. 'Supply Chain', 'Finance', 'Sales & Commercial').",
    )
    subdomain: Optional[str] = Field(
        default=None,
        description="Optional subdomain filter (e.g. 'Planning & Forecasting', 'Procurement').",
    )
    limit: int = Field(default=10, ge=1, le=30, description="Max metric views to return.")


@tool(
    name="search_metric_views",
    description=(
        "Search and discover governed Metric Views, business KPIs, and related dashboards/apps. "
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
