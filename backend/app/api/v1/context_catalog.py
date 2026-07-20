"""
Context Catalog API.

Admin CRUD for context domains and documents, plus a retrieval endpoint shared
with the agent tools. Writes require Platform/Governance Admin; reads and search
are available to any authenticated user (and, via the tools, to the agent).
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.core.feature_flags import is_feature_enabled
from app.models.user import User
from app.providers.context_catalog import (
    DocumentParseError,
    ContextCatalogStorage,
    detect_doc_type,
    parse_document,
)
from app.services.context_catalog_service import ContextCatalogService

logger = logging.getLogger(__name__)

router = APIRouter()

_WRITE_ROLES = ["Platform Admin", "Governance Admin"]


def _require_feature() -> None:
    if not is_feature_enabled("context_catalog"):
        raise HTTPException(status_code=404, detail="Context Catalog is not enabled")


# --- Schemas ---

class DomainCreate(BaseModel):
    name: str = Field(..., description="Domain name")
    description: Optional[str] = None
    parent_id: Optional[str] = Field(default=None, description="Parent domain id for sub-domains")
    domain_type: str = Field(default="community", description="community or system")
    primary_owner: Optional[str] = None
    secondary_owner: Optional[str] = None
    reviewers: Optional[List[str]] = None
    categories: Optional[List[str]] = None


class DomainUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[str] = None
    domain_type: Optional[str] = None
    primary_owner: Optional[str] = None
    secondary_owner: Optional[str] = None
    reviewers: Optional[List[str]] = None
    categories: Optional[List[str]] = None


class DocumentCreate(BaseModel):
    title: str = Field(..., description="Document title")
    body_markdown: Optional[str] = Field(default="", description="Markdown body")
    doc_type: str = Field(default="markdown", description="markdown or link")
    source_url: Optional[str] = Field(default=None, description="External URL for link-type docs")
    status: str = Field(default="published", description="draft or published")
    tags: Optional[List[str]] = None


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    body_markdown: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None
    source_url: Optional[str] = None
    domain_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2, description="Search query")
    domain_slug: Optional[str] = Field(default=None, description="Restrict to a domain (and its sub-domains)")
    limit: Optional[int] = Field(default=None, description="Max results")


class ContextImportRequest(BaseModel):
    bundle: Dict[str, Any]
    doc_status: str = Field(
        default="keep",
        description="keep (preserve exported status) | draft | published",
    )
    overwrite: bool = True


# --- Domain endpoints ---

@router.get("/domains")
def list_domains(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    domains = ContextCatalogService.list_domains(db)
    return [ContextCatalogService.domain_to_dict(db, d) for d in domains]


@router.post("/domains")
def create_domain(
    *,
    db: Session = Depends(deps.get_db),
    body: DomainCreate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        domain = ContextCatalogService.create_domain(
            db, created_by=current_user.email, **body.model_dump()
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ContextCatalogService.domain_to_dict(db, domain)


@router.get("/domains/{domain_id}")
def get_domain(
    domain_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    domain = ContextCatalogService.get_domain(db, domain_id)
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")
    data = ContextCatalogService.domain_to_dict(db, domain)
    docs = ContextCatalogService.list_documents(db, domain_id)
    data["documents"] = [ContextCatalogService.document_to_dict(d, include_body=False) for d in docs]
    return data


@router.put("/domains/{domain_id}")
def update_domain(
    *,
    domain_id: str,
    db: Session = Depends(deps.get_db),
    body: DomainUpdate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        domain = ContextCatalogService.update_domain(db, domain_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ContextCatalogService.domain_to_dict(db, domain)


@router.delete("/domains/{domain_id}")
def delete_domain(
    *,
    domain_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        ContextCatalogService.delete_domain(db, domain_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True}


# --- Document endpoints ---

@router.post("/domains/{domain_id}/documents")
def create_document(
    *,
    domain_id: str,
    db: Session = Depends(deps.get_db),
    body: DocumentCreate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        document = ContextCatalogService.create_document(
            db,
            domain_id=domain_id,
            title=body.title,
            body_markdown=body.body_markdown,
            doc_type=body.doc_type,
            source_url=body.source_url,
            status=body.status,
            tags=body.tags,
            created_by=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ContextCatalogService.document_to_dict(document)


@router.post("/domains/{domain_id}/documents/upload")
async def upload_document(
    *,
    domain_id: str,
    db: Session = Depends(deps.get_db),
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    status: str = Form(default="published"),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    if not ContextCatalogService.get_domain(db, domain_id):
        raise HTTPException(status_code=400, detail="Domain not found")

    content = await file.read()
    max_bytes = settings.CONTEXT_CATALOG_MAX_UPLOAD_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds the {settings.CONTEXT_CATALOG_MAX_UPLOAD_MB} MB limit",
        )

    try:
        doc_type = detect_doc_type(file.filename)
        body_markdown = parse_document(file.filename, content)
    except DocumentParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    document = ContextCatalogService.create_document(
        db,
        domain_id=domain_id,
        title=title or file.filename,
        body_markdown=body_markdown,
        doc_type=doc_type,
        source_filename=file.filename,
        status=status,
        created_by=current_user.email,
    )

    # Best-effort storage of the original on a UC Volume (no-op if unconfigured).
    storage = ContextCatalogStorage()
    if storage.enabled:
        path = storage.store_original(document.id, file.filename, content)
        if path:
            document.storage_path = path
            db.add(document)
            db.commit()
            db.refresh(document)

    return ContextCatalogService.document_to_dict(document)


@router.get("/documents/{document_id}")
def get_document(
    document_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    document = ContextCatalogService.get_document(db, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return ContextCatalogService.document_to_dict(document)


@router.put("/documents/{document_id}")
def update_document(
    *,
    document_id: str,
    db: Session = Depends(deps.get_db),
    body: DocumentUpdate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        document = ContextCatalogService.update_document(
            db, document_id, **body.model_dump(exclude_unset=True)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ContextCatalogService.document_to_dict(document)


@router.delete("/documents/{document_id}")
def delete_document(
    *,
    document_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        storage_path = ContextCatalogService.delete_document(db, document_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if storage_path:
        ContextCatalogStorage().delete_original(storage_path)
    return {"success": True}


# --- Retrieval ---

@router.post("/search")
def search_catalog(
    *,
    db: Session = Depends(deps.get_db),
    body: SearchRequest,
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    results = ContextCatalogService.search(
        db, body.query, domain_slug=body.domain_slug, limit=body.limit
    )
    return {"query": body.query, "results": results}


# --- Export / import (promote the catalog across environments) ---

@router.get("/export/bundle")
def export_context_bundle(
    domain_ids: Optional[str] = None,
    published_only: bool = False,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Export domains + documents as a portable, env-agnostic JSON bundle.

    Pass ``domain_ids`` (comma-separated) to export only those domains and their
    descendants; omit it to export the whole catalog.
    """
    id_list = [i for i in (domain_ids.split(",") if domain_ids else []) if i.strip()]
    return ContextCatalogService.export_bundle(
        db, domain_ids=id_list or None, published_only=published_only
    )


@router.post("/import/bundle")
def import_context_bundle(
    *,
    body: ContextImportRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Import a bundle into this environment (upsert domains by slug, documents by title)."""
    try:
        return ContextCatalogService.import_bundle(
            db, body.bundle,
            doc_status=body.doc_status, overwrite=body.overwrite,
            created_by=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
