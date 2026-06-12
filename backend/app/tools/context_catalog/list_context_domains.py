"""
Tool to list the available Context Catalog domains.
"""
from typing import Dict, Any
from pydantic import BaseModel

from app.tools.mcp import tool
from app.db.session import get_db
from app.services.context_catalog_service import ContextCatalogService


class ListContextDomainsInput(BaseModel):
    pass


@tool(
    name="list_context_domains",
    description=(
        "List the available Context Catalog domains (curated, company-specific "
        "knowledge areas). Use this to discover what subjects the catalog covers "
        "before deciding whether to call search_context_catalog with a domain filter."
    ),
    args_schema=ListContextDomainsInput,
    feature_flag="context_catalog",
    friendly_label="Browsing the context catalog...",
)
def list_context_domains() -> Dict[str, Any]:
    """Return the catalog domains with their descriptions and document counts."""
    db = next(get_db())
    try:
        domains = ContextCatalogService.list_domains(db)
        items = []
        for d in domains:
            data = ContextCatalogService.domain_to_dict(db, d)
            items.append({
                "slug": data["slug"],
                "name": data["name"],
                "description": data["description"],
                "parent_id": data["parent_id"],
                "document_count": data.get("document_count", 0),
            })
        return {"count": len(items), "domains": items}
    finally:
        db.close()
