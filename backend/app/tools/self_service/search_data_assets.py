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
            "(e.g. 'cancel pushout', 'sales orders', 'customer retention'). "
            "Matched against the table name, fully-qualified name, description, "
            "owner, catalog, schema, domain, and tags."
        ),
    )
    asset_type: Optional[str] = Field(
        default=None,
        description="Optional filter by asset type, e.g. 'VIEW', 'MANAGED', 'EXTERNAL', 'DATA_PRODUCT'.",
    )
    domain: Optional[str] = Field(
        default=None,
        description="Optional domain filter (e.g. 'Core', 'Analytics').",
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
        "Search the locally-cached data catalog (Unity Catalog tables/views that "
        "have been synced into this app) by keyword. FAST — it scans the local "
        "database with no live Databricks calls, so prefer it as the FIRST step "
        "for data-discovery questions ('what data is there about X?', 'where is "
        "the Y table?', 'do we have data on Z?'). Matches on name, "
        "fully-qualified name, description, owner, catalog, schema, domain, and "
        "tags, and returns catalog/schema/table, type, owner, description, "
        "domain, tags, and certification. Fall back to the live metadata tools "
        "(get_table_list / "
        "get_schema_list) only if this returns nothing, and use ask_your_data "
        "(Genie) when the user needs actual rows/analysis."
    ),
    args_schema=SearchDataAssetsInput,
    feature_flag="data_discovery",
    friendly_label="Scanning the data catalog...",
)
def search_data_assets(
    query: str,
    asset_type: Optional[str] = None,
    domain: Optional[str] = None,
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
            # Tags are JSON (SQLite) / JSONB (Postgres); cast to text so a plain
            # ILIKE works the same on both — matches the tag NAMES we store.
            cast(DataAssetModel.tags, String),
        ]

        q = db.query(DataAssetModel)
        if asset_type:
            q = q.filter(DataAssetModel.type.ilike(f"%{asset_type}%"))
        if domain:
            q = q.filter(DataAssetModel.domain.ilike(f"%{domain}%"))
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
                    tag_text,
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
            return score

        candidates.sort(key=_score, reverse=True)
        top = candidates[:limit]
        assets = [_serialize(a) for a in top]

        if assets:
            note = (
                "Cached UC tables/views from the local data catalog. Offer to "
                "summarize a candidate, find_owner, help request access, or run "
                "ask_your_data (Genie) for the actual rows/analysis. Reference "
                "assets by their fully-qualified id (catalog.schema.table)."
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
