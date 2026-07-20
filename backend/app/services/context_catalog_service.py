"""
Context Catalog service.

Holds the business logic for managing context domains/documents and for the
lightweight keyword retrieval the agent uses. Kept separate from the API layer
so the agent tools can reuse the exact same retrieval path.

Retrieval is intentionally simple: documents are split into chunks on write,
and a query is scored against chunks with case-insensitive term matching
(portable across SQLite and Postgres). There is no vector index — the catalog
is expected to stay small enough that keyword search is sufficient.
"""
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.context_catalog import (
    ContextChunkModel,
    ContextDocumentModel,
    ContextDomainModel,
)

logger = logging.getLogger(__name__)

# Portable bundle format tag (bumped if the export shape changes). Mirrors the
# workflow bundle convention so domains/documents promote cleanly across envs.
CONTEXT_BUNDLE_FORMAT = "selfservice.context_catalog/v1"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")
    return slug or "domain"


def _tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens of length >= 2."""
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 2]


def _chunk_text(text: str, chunk_size: int) -> List[str]:
    """Split text into ~chunk_size character chunks on paragraph boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        # A single oversized paragraph is hard-split so no chunk blows past the limit.
        if len(para) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(para), chunk_size):
                chunks.append(para[i:i + chunk_size])
            continue
        if not current:
            current = para
        elif len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}"
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks


class ContextCatalogService:
    """CRUD + retrieval for the Context Catalog."""

    # ------------------------------------------------------------------ domains

    @staticmethod
    def _unique_slug(db: Session, name: str, exclude_id: Optional[str] = None) -> str:
        base = _slugify(name)
        slug = base
        suffix = 2
        while True:
            q = db.query(ContextDomainModel).filter(ContextDomainModel.slug == slug)
            if exclude_id:
                q = q.filter(ContextDomainModel.id != exclude_id)
            if not q.first():
                return slug
            slug = f"{base}-{suffix}"
            suffix += 1

    @staticmethod
    def create_domain(db: Session, *, name: str, description: Optional[str] = None,
                      parent_id: Optional[str] = None, domain_type: str = "community",
                      primary_owner: Optional[str] = None, secondary_owner: Optional[str] = None,
                      reviewers: Optional[list] = None, categories: Optional[list] = None,
                      created_by: Optional[str] = None) -> ContextDomainModel:
        if parent_id:
            parent = db.query(ContextDomainModel).filter(ContextDomainModel.id == parent_id).first()
            if not parent:
                raise ValueError("Parent domain not found")
        domain = ContextDomainModel(
            id=str(uuid.uuid4()),
            slug=ContextCatalogService._unique_slug(db, name),
            name=name,
            description=description,
            parent_id=parent_id,
            domain_type=domain_type or "community",
            primary_owner=primary_owner,
            secondary_owner=secondary_owner,
            reviewers=reviewers or [],
            categories=categories or [],
            created_by=created_by,
        )
        db.add(domain)
        db.commit()
        db.refresh(domain)
        return domain

    @staticmethod
    def list_domains(db: Session) -> List[ContextDomainModel]:
        return db.query(ContextDomainModel).order_by(ContextDomainModel.name).all()

    @staticmethod
    def get_domain(db: Session, domain_id: str) -> Optional[ContextDomainModel]:
        return db.query(ContextDomainModel).filter(ContextDomainModel.id == domain_id).first()

    @staticmethod
    def get_domain_by_slug(db: Session, slug: str) -> Optional[ContextDomainModel]:
        return db.query(ContextDomainModel).filter(ContextDomainModel.slug == slug).first()

    @staticmethod
    def update_domain(db: Session, domain_id: str, **fields) -> ContextDomainModel:
        domain = ContextCatalogService.get_domain(db, domain_id)
        if not domain:
            raise ValueError("Domain not found")
        if fields.get("name") and fields["name"] != domain.name:
            domain.name = fields["name"]
        for key in ("description", "parent_id", "domain_type", "primary_owner",
                    "secondary_owner", "reviewers", "categories"):
            if key in fields and fields[key] is not None:
                setattr(domain, key, fields[key])
        # Guard against a domain becoming its own ancestor.
        if fields.get("parent_id"):
            if fields["parent_id"] == domain_id:
                raise ValueError("A domain cannot be its own parent")
        db.add(domain)
        db.commit()
        db.refresh(domain)
        return domain

    @staticmethod
    def _descendant_ids(db: Session, domain_id: str) -> List[str]:
        """Return domain_id plus all descendant domain ids (BFS)."""
        all_domains = db.query(ContextDomainModel.id, ContextDomainModel.parent_id).all()
        children: Dict[Optional[str], List[str]] = {}
        for did, pid in all_domains:
            children.setdefault(pid, []).append(did)
        result = [domain_id]
        queue = [domain_id]
        while queue:
            current = queue.pop()
            for child in children.get(current, []):
                result.append(child)
                queue.append(child)
        return result

    @staticmethod
    def delete_domain(db: Session, domain_id: str) -> None:
        domain = ContextCatalogService.get_domain(db, domain_id)
        if not domain:
            raise ValueError("Domain not found")
        ids = ContextCatalogService._descendant_ids(db, domain_id)
        db.query(ContextChunkModel).filter(ContextChunkModel.domain_id.in_(ids)).delete(synchronize_session=False)
        db.query(ContextDocumentModel).filter(ContextDocumentModel.domain_id.in_(ids)).delete(synchronize_session=False)
        db.query(ContextDomainModel).filter(ContextDomainModel.id.in_(ids)).delete(synchronize_session=False)
        db.commit()

    # ---------------------------------------------------------------- documents

    @staticmethod
    def _rebuild_chunks(db: Session, document: ContextDocumentModel) -> None:
        db.query(ContextChunkModel).filter(ContextChunkModel.document_id == document.id).delete(
            synchronize_session=False
        )
        if document.status != "published":
            return
        # Prepend the title so title terms are retrievable alongside the body.
        body = f"{document.title}\n\n{document.body_markdown or ''}"
        for idx, content in enumerate(_chunk_text(body, settings.CONTEXT_CATALOG_CHUNK_SIZE)):
            db.add(ContextChunkModel(
                id=str(uuid.uuid4()),
                document_id=document.id,
                domain_id=document.domain_id,
                chunk_index=idx,
                content=content,
            ))

    @staticmethod
    def create_document(db: Session, *, domain_id: str, title: str,
                        body_markdown: Optional[str] = None, doc_type: str = "markdown",
                        source_filename: Optional[str] = None, source_url: Optional[str] = None,
                        storage_path: Optional[str] = None, status: str = "published",
                        tags: Optional[list] = None, created_by: Optional[str] = None) -> ContextDocumentModel:
        if not ContextCatalogService.get_domain(db, domain_id):
            raise ValueError("Domain not found")
        document = ContextDocumentModel(
            id=str(uuid.uuid4()),
            domain_id=domain_id,
            title=title,
            doc_type=doc_type or "markdown",
            source_filename=source_filename,
            source_url=source_url,
            storage_path=storage_path,
            body_markdown=body_markdown,
            status=status or "published",
            tags=tags or [],
            created_by=created_by,
        )
        db.add(document)
        db.flush()
        ContextCatalogService._rebuild_chunks(db, document)
        db.commit()
        db.refresh(document)
        return document

    @staticmethod
    def list_documents(db: Session, domain_id: Optional[str] = None) -> List[ContextDocumentModel]:
        q = db.query(ContextDocumentModel)
        if domain_id:
            q = q.filter(ContextDocumentModel.domain_id == domain_id)
        return q.order_by(ContextDocumentModel.updated_at.desc()).all()

    @staticmethod
    def get_document(db: Session, document_id: str) -> Optional[ContextDocumentModel]:
        return db.query(ContextDocumentModel).filter(ContextDocumentModel.id == document_id).first()

    @staticmethod
    def update_document(db: Session, document_id: str, **fields) -> ContextDocumentModel:
        document = ContextCatalogService.get_document(db, document_id)
        if not document:
            raise ValueError("Document not found")
        rebuild = False
        for key in ("title", "body_markdown", "status", "tags", "source_url", "domain_id"):
            if key in fields and fields[key] is not None:
                setattr(document, key, fields[key])
                if key in ("title", "body_markdown", "status", "domain_id"):
                    rebuild = True
        if fields.get("domain_id") and not ContextCatalogService.get_domain(db, fields["domain_id"]):
            raise ValueError("Domain not found")
        db.add(document)
        db.flush()
        if rebuild:
            ContextCatalogService._rebuild_chunks(db, document)
        db.commit()
        db.refresh(document)
        return document

    @staticmethod
    def delete_document(db: Session, document_id: str) -> Optional[str]:
        document = ContextCatalogService.get_document(db, document_id)
        if not document:
            raise ValueError("Document not found")
        storage_path = document.storage_path
        db.query(ContextChunkModel).filter(ContextChunkModel.document_id == document_id).delete(
            synchronize_session=False
        )
        db.query(ContextDocumentModel).filter(ContextDocumentModel.id == document_id).delete(
            synchronize_session=False
        )
        db.commit()
        return storage_path

    # ---------------------------------------------------------------- retrieval

    @staticmethod
    def search(db: Session, query: str, *, domain_slug: Optional[str] = None,
               limit: Optional[int] = None, track_usage: bool = False) -> List[Dict[str, Any]]:
        """Keyword search over published document chunks.

        Returns ranked chunks with enough metadata to cite the source.

        When ``track_usage`` is True, the documents that appear in the returned
        results have their retrieval-usage counters bumped (best-effort). Pass
        it from the *agent* retrieval path only — admin UI search should leave
        it False so browsing doesn't inflate the usage signal.
        """
        limit = limit or settings.CONTEXT_CATALOG_SEARCH_LIMIT
        terms = _tokenize(query)
        if not terms:
            return []

        domain_ids: Optional[List[str]] = None
        if domain_slug:
            domain = ContextCatalogService.get_domain_by_slug(db, domain_slug)
            if not domain:
                return []
            domain_ids = ContextCatalogService._descendant_ids(db, domain.id)

        q = (
            db.query(ContextChunkModel, ContextDocumentModel, ContextDomainModel)
            .join(ContextDocumentModel, ContextChunkModel.document_id == ContextDocumentModel.id)
            .join(ContextDomainModel, ContextChunkModel.domain_id == ContextDomainModel.id)
            .filter(ContextDocumentModel.status == "published")
            .filter(or_(*[ContextChunkModel.content.ilike(f"%{t}%") for t in terms]))
        )
        if domain_ids is not None:
            q = q.filter(ContextChunkModel.domain_id.in_(domain_ids))

        # Cap the candidate set; small catalog so this is plenty.
        candidates = q.limit(500).all()

        scored: List[Dict[str, Any]] = []
        for chunk, document, domain in candidates:
            content_lower = chunk.content.lower()
            title_lower = (document.title or "").lower()
            score = 0
            matched_terms = 0
            for term in terms:
                occ = content_lower.count(term)
                if occ:
                    matched_terms += 1
                    score += occ
                if term in title_lower:
                    score += 3  # title hits are strong signals
            if score == 0:
                continue
            # Reward chunks that match more distinct query terms.
            score += matched_terms * 2
            scored.append({
                "score": score,
                "document_id": document.id,
                "document_title": document.title,
                "doc_type": document.doc_type,
                "source_filename": document.source_filename,
                "source_url": document.source_url,
                "domain_id": domain.id,
                "domain_name": domain.name,
                "domain_slug": domain.slug,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
            })

        scored.sort(key=lambda r: r["score"], reverse=True)
        results = scored[:limit]

        if track_usage and results:
            ContextCatalogService._bump_retrieval_usage(
                db, [r["document_id"] for r in results]
            )

        return results

    @staticmethod
    def _bump_retrieval_usage(db: Session, document_ids: List[str]) -> None:
        """Increment retrieval counters for the given documents (best-effort).

        Never raises: a failure to record the usage signal must not break the
        retrieval the agent depends on. Distinct ids only, so a doc that matched
        via several chunks is counted once per search.
        """
        unique_ids = list(dict.fromkeys(document_ids))
        if not unique_ids:
            return
        try:
            now = datetime.utcnow()
            (
                db.query(ContextDocumentModel)
                .filter(ContextDocumentModel.id.in_(unique_ids))
                .update(
                    {
                        ContextDocumentModel.retrieval_count: (
                            ContextDocumentModel.retrieval_count + 1
                        ),
                        ContextDocumentModel.last_retrieved_at: now,
                    },
                    synchronize_session=False,
                )
            )
            db.commit()
        except Exception as e:  # noqa: BLE001 - usage signal is non-critical
            logger.warning("Context Catalog: failed to record retrieval usage: %s", e)
            db.rollback()

    # ------------------------------------------------------- export / import (envs)

    @staticmethod
    def export_bundle(
        db: Session,
        *,
        domain_ids: Optional[List[str]] = None,
        published_only: bool = False,
    ) -> Dict[str, Any]:
        """Build a portable, env-agnostic bundle of context domains + documents.

        Domains are keyed by ``slug`` and reference their parent via
        ``parent_slug`` (not the env-specific UUID) so the tree re-links on
        import. Documents nest under their owning domain and are matched by title
        within that domain. When ``domain_ids`` is given, the export includes
        those domains **and all their descendants** so parent refs stay
        resolvable; otherwise every domain is exported. ``published_only`` limits
        the exported documents to published ones (domains are always included).
        """
        from datetime import datetime as _dt

        all_domains = db.query(ContextDomainModel).all()
        by_id = {d.id: d for d in all_domains}

        if domain_ids:
            selected: set = set()
            for did in domain_ids:
                selected.update(ContextCatalogService._descendant_ids(db, did))
            domains = [d for d in all_domains if d.id in selected]
        else:
            domains = list(all_domains)

        domains.sort(key=lambda d: (d.name or "").lower())

        out_domains: List[Dict[str, Any]] = []
        for d in domains:
            docs_q = db.query(ContextDocumentModel).filter(
                ContextDocumentModel.domain_id == d.id
            )
            if published_only:
                docs_q = docs_q.filter(ContextDocumentModel.status == "published")
            docs = docs_q.order_by(ContextDocumentModel.title.asc()).all()
            parent = by_id.get(d.parent_id) if d.parent_id else None
            out_domains.append({
                "slug": d.slug,
                "name": d.name,
                "description": d.description,
                "parent_slug": parent.slug if parent else None,
                "domain_type": d.domain_type,
                "primary_owner": d.primary_owner,
                "secondary_owner": d.secondary_owner,
                "reviewers": d.reviewers or [],
                "categories": d.categories or [],
                "documents": [
                    {
                        "title": doc.title,
                        "doc_type": doc.doc_type,
                        "source_url": doc.source_url,
                        "source_filename": doc.source_filename,
                        "status": doc.status,
                        "tags": doc.tags or [],
                        "body_markdown": doc.body_markdown,
                    }
                    for doc in docs
                ],
            })

        return {
            "format": CONTEXT_BUNDLE_FORMAT,
            "exported_at": _dt.utcnow().isoformat(),
            "domains": out_domains,
        }

    @staticmethod
    def import_bundle(
        db: Session,
        bundle: Dict[str, Any],
        *,
        doc_status: str = "keep",
        overwrite: bool = True,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upsert domains (by slug) and their documents (by title within a domain).

        - ``doc_status``: ``keep`` preserves each document's exported status;
          ``draft`` / ``published`` forces all imported docs to that status.
        - ``overwrite``: when True, existing domains/documents are updated in
          place; when False they're left untouched and reported as skipped.

        Parent links are resolved by ``parent_slug`` in a second pass after all
        domains exist, so ordering within the bundle doesn't matter. Chunks are
        rebuilt for every created/updated document. Commits once at the end.
        """
        if not isinstance(bundle, dict) or bundle.get("format") != CONTEXT_BUNDLE_FORMAT:
            raise ValueError(
                f"Unrecognized bundle format (expected {CONTEXT_BUNDLE_FORMAT})"
            )
        entries = bundle.get("domains")
        if not isinstance(entries, list):
            raise ValueError("Bundle 'domains' must be a list")
        if doc_status not in ("keep", "draft", "published"):
            raise ValueError("doc_status must be 'keep', 'draft', or 'published'")

        report: Dict[str, Any] = {
            "domains": {"created": [], "updated": [], "skipped": []},
            "documents": {"created": [], "updated": [], "skipped": []},
            "errors": [],
        }

        # Pass 1: upsert domains (without parent), building a slug -> model map.
        slug_map: Dict[str, ContextDomainModel] = {}
        parent_by_slug: Dict[str, Optional[str]] = {}
        for entry in entries:
            entry = entry or {}
            slug = (entry.get("slug") or _slugify(entry.get("name") or "")).strip()
            name = entry.get("name") or slug
            if not slug:
                report["errors"].append({"domain": None, "error": "missing slug/name"})
                continue
            parent_by_slug[slug] = entry.get("parent_slug")
            fields = {
                "name": name,
                "description": entry.get("description"),
                "domain_type": entry.get("domain_type") or "community",
                "primary_owner": entry.get("primary_owner"),
                "secondary_owner": entry.get("secondary_owner"),
                "reviewers": entry.get("reviewers") or [],
                "categories": entry.get("categories") or [],
            }
            existing = ContextCatalogService.get_domain_by_slug(db, slug)
            if existing:
                slug_map[slug] = existing
                if overwrite:
                    for key, value in fields.items():
                        setattr(existing, key, value)
                    db.add(existing)
                    report["domains"]["updated"].append(slug)
                else:
                    report["domains"]["skipped"].append(slug)
            else:
                domain = ContextDomainModel(
                    id=str(uuid.uuid4()),
                    slug=slug,
                    created_by=created_by,
                    **fields,
                )
                db.add(domain)
                slug_map[slug] = domain
                report["domains"]["created"].append(slug)
        db.flush()

        # Pass 2: resolve parent links by slug (guard self-parent).
        for slug, domain in slug_map.items():
            parent_slug = parent_by_slug.get(slug)
            if not parent_slug:
                continue
            parent = slug_map.get(parent_slug) or ContextCatalogService.get_domain_by_slug(
                db, parent_slug
            )
            if parent and parent.id != domain.id:
                domain.parent_id = parent.id
                db.add(domain)
            else:
                report["errors"].append({
                    "domain": slug,
                    "error": f"parent '{parent_slug}' not found; imported at top level",
                })
        db.flush()

        # Pass 3: upsert documents within each domain (by title).
        for entry in entries:
            entry = entry or {}
            slug = (entry.get("slug") or _slugify(entry.get("name") or "")).strip()
            domain = slug_map.get(slug)
            if not domain:
                continue
            for doc in entry.get("documents") or []:
                doc = doc or {}
                title = doc.get("title")
                if not title:
                    report["errors"].append({"domain": slug, "error": "document missing title"})
                    continue
                status = doc.get("status") or "published"
                if doc_status != "keep":
                    status = doc_status
                key = f"{slug}/{title}"
                existing_doc = (
                    db.query(ContextDocumentModel)
                    .filter(
                        ContextDocumentModel.domain_id == domain.id,
                        ContextDocumentModel.title == title,
                    )
                    .first()
                )
                if existing_doc:
                    if not overwrite:
                        report["documents"]["skipped"].append(key)
                        continue
                    existing_doc.body_markdown = doc.get("body_markdown")
                    existing_doc.doc_type = doc.get("doc_type") or "markdown"
                    existing_doc.source_url = doc.get("source_url")
                    existing_doc.source_filename = doc.get("source_filename")
                    existing_doc.status = status
                    existing_doc.tags = doc.get("tags") or []
                    db.add(existing_doc)
                    db.flush()
                    ContextCatalogService._rebuild_chunks(db, existing_doc)
                    report["documents"]["updated"].append(key)
                else:
                    new_doc = ContextDocumentModel(
                        id=str(uuid.uuid4()),
                        domain_id=domain.id,
                        title=title,
                        doc_type=doc.get("doc_type") or "markdown",
                        source_url=doc.get("source_url"),
                        source_filename=doc.get("source_filename"),
                        body_markdown=doc.get("body_markdown"),
                        status=status,
                        tags=doc.get("tags") or [],
                        created_by=created_by,
                    )
                    db.add(new_doc)
                    db.flush()
                    ContextCatalogService._rebuild_chunks(db, new_doc)
                    report["documents"]["created"].append(key)

        db.commit()
        return report

    # ------------------------------------------------------------ serialization

    @staticmethod
    def domain_to_dict(db: Session, domain: ContextDomainModel,
                       include_counts: bool = True) -> Dict[str, Any]:
        data = {
            "id": domain.id,
            "slug": domain.slug,
            "name": domain.name,
            "description": domain.description,
            "parent_id": domain.parent_id,
            "domain_type": domain.domain_type,
            "primary_owner": domain.primary_owner,
            "secondary_owner": domain.secondary_owner,
            "reviewers": domain.reviewers or [],
            "categories": domain.categories or [],
            "created_by": domain.created_by,
            "created_at": domain.created_at.isoformat() if domain.created_at else None,
            "updated_at": domain.updated_at.isoformat() if domain.updated_at else None,
        }
        if include_counts:
            data["document_count"] = (
                db.query(ContextDocumentModel)
                .filter(ContextDocumentModel.domain_id == domain.id)
                .count()
            )
            # Domain-level usage signal: total agent retrievals across this
            # domain's documents (direct children only, matching document_count).
            data["retrieval_count"] = (
                db.query(func.coalesce(func.sum(ContextDocumentModel.retrieval_count), 0))
                .filter(ContextDocumentModel.domain_id == domain.id)
                .scalar()
            ) or 0
        return data

    @staticmethod
    def document_to_dict(document: ContextDocumentModel, include_body: bool = True) -> Dict[str, Any]:
        data = {
            "id": document.id,
            "domain_id": document.domain_id,
            "title": document.title,
            "doc_type": document.doc_type,
            "source_filename": document.source_filename,
            "source_url": document.source_url,
            "storage_path": document.storage_path,
            "status": document.status,
            "tags": document.tags or [],
            "created_by": document.created_by,
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "updated_at": document.updated_at.isoformat() if document.updated_at else None,
            "retrieval_count": document.retrieval_count or 0,
            "last_retrieved_at": (
                document.last_retrieved_at.isoformat()
                if document.last_retrieved_at
                else None
            ),
        }
        if include_body:
            data["body_markdown"] = document.body_markdown
        else:
            body = document.body_markdown or ""
            data["preview"] = body[:280] + ("…" if len(body) > 280 else "")
        return data
