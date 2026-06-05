"""
Tool to retrieve relevant passages from the Context Catalog.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.db.session import get_db
from app.services.context_catalog_service import ContextCatalogService


class SearchContextCatalogInput(BaseModel):
    query: str = Field(
        ...,
        min_length=2,
        description="The natural-language question or keywords to search the curated knowledge base for.",
    )
    domain_slug: Optional[str] = Field(
        default=None,
        description="Optional domain slug (from list_context_domains) to restrict the search to that domain and its sub-domains.",
    )


@tool(
    name="search_context_catalog",
    description=(
        "Search the Context Catalog — a curated knowledge base of company- and "
        "domain-specific documents — and return the most relevant passages with "
        "citations. Use this BEFORE answering questions about internal processes, "
        "standards, products, or domain knowledge that generic Databricks docs "
        "wouldn't cover. Always cite the returned document titles."
    ),
    args_schema=SearchContextCatalogInput,
    feature_flag="context_catalog",
    friendly_label="Searching the context catalog...",
)
async def search_context_catalog(query: str, domain_slug: Optional[str] = None) -> Dict[str, Any]:
    """Return ranked passages matching the query."""
    db = next(get_db())
    try:
        results = ContextCatalogService.search(db, query, domain_slug=domain_slug)
        passages = [
            {
                "document_id": r["document_id"],
                "document_title": r["document_title"],
                "domain": r["domain_name"],
                "domain_slug": r["domain_slug"],
                "content": r["content"],
                "source_url": r.get("source_url"),
            }
            for r in results
        ]
        return {
            "query": query,
            "count": len(passages),
            "passages": passages,
            "note": (
                "No matching context found. Answer from general knowledge or ask a clarifying question."
                if not passages
                else "Cite the document_title(s) when you use these passages."
            ),
        }
    finally:
        db.close()
