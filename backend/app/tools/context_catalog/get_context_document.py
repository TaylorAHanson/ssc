"""
Tool to fetch the full body of a single Context Catalog document.
"""
from typing import Dict, Any
from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.db.session import get_db
from app.services.context_catalog_service import ContextCatalogService


class GetContextDocumentInput(BaseModel):
    document_id: str = Field(
        ...,
        description="The id of the document to fetch (returned by search_context_catalog passages).",
    )


@tool(
    name="get_context_document",
    description=(
        "Fetch the full markdown body of a single Context Catalog document by id. "
        "Use this after search_context_catalog when a passage looks relevant and you "
        "need the complete document for detail."
    ),
    args_schema=GetContextDocumentInput,
    feature_flag="context_catalog",
    friendly_label="Opening context document...",
)
async def get_context_document(document_id: str) -> Dict[str, Any]:
    """Return the full document, or an error if not found / not published."""
    db = next(get_db())
    try:
        document = ContextCatalogService.get_document(db, document_id)
        if not document:
            return {"found": False, "error": f"Document '{document_id}' not found."}
        if document.status != "published":
            return {"found": False, "error": "Document is a draft and not available."}
        return {
            "found": True,
            "document": {
                "id": document.id,
                "title": document.title,
                "domain_id": document.domain_id,
                "doc_type": document.doc_type,
                "source_url": document.source_url,
                "body_markdown": document.body_markdown,
            },
        }
    finally:
        db.close()
