"""
Database models for the Context Catalog.

The Context Catalog is a curated knowledge base of "context domains" (an
arbitrarily-deep tree) and the documents that live inside them. Admins author
markdown directly or upload docs (docx/pptx/pdf) whose text is extracted on
ingest. Documents are split into ``ContextChunkModel`` rows so the agent can
retrieve only the relevant passages instead of injecting whole files into the
prompt.

Retrieval today is lightweight keyword/full-text matching over the chunk table
(portable across SQLite locally and Lakebase/Postgres in prod). The schema
leaves room to add vector embeddings later without a breaking change.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, String, DateTime, Text, Integer, JSON, ForeignKey
from sqlalchemy.orm import Mapped, relationship

from app.db.base import Base


class ContextDomainModel(Base):
    """A node in the context-domain tree (e.g. "GTM Operations" > "SSA")."""

    __tablename__ = "context_domains"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the domain")
    slug: Mapped[str] = Column(String, nullable=False, unique=True, index=True, comment="URL-safe unique identifier")
    name: Mapped[str] = Column(String, nullable=False, comment="Human-readable domain name")
    description: Mapped[Optional[str]] = Column(Text, nullable=True, comment="What this domain is for")
    parent_id: Mapped[Optional[str]] = Column(
        String, ForeignKey("context_domains.id"), nullable=True, index=True,
        comment="Parent domain id for sub-domains; null for top-level domains",
    )
    domain_type: Mapped[str] = Column(
        String, nullable=False, default="community", index=True,
        comment="community (user-created) or system (built-in categories)",
    )
    primary_owner: Mapped[Optional[str]] = Column(String, nullable=True, comment="Primary owner (name or email)")
    secondary_owner: Mapped[Optional[str]] = Column(String, nullable=True, comment="Secondary owner (name or email)")
    reviewers: Mapped[Optional[list]] = Column(JSON, nullable=True, comment="List of reviewer names/emails")
    categories: Mapped[Optional[list]] = Column(JSON, nullable=True, comment="Free-form category labels")
    created_by: Mapped[Optional[str]] = Column(String, nullable=True, comment="Email of the admin who created it")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ContextDocumentModel(Base):
    """A single context document inside a domain (markdown, uploaded file, or link)."""

    __tablename__ = "context_documents"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the document")
    domain_id: Mapped[str] = Column(
        String, ForeignKey("context_domains.id"), nullable=False, index=True,
        comment="Owning domain id",
    )
    title: Mapped[str] = Column(String, nullable=False, comment="Document title")
    doc_type: Mapped[str] = Column(
        String, nullable=False, default="markdown", index=True,
        comment="markdown | docx | pptx | pdf | link",
    )
    source_filename: Mapped[Optional[str]] = Column(String, nullable=True, comment="Original uploaded filename")
    source_url: Mapped[Optional[str]] = Column(String, nullable=True, comment="External URL for link-type docs")
    storage_path: Mapped[Optional[str]] = Column(String, nullable=True, comment="UC Volume path of the stored original, if any")
    body_markdown: Mapped[Optional[str]] = Column(Text, nullable=True, comment="Native or extracted markdown/text used for retrieval")
    status: Mapped[str] = Column(
        String, nullable=False, default="published", index=True,
        comment="draft | published (only published docs are retrievable by the agent)",
    )
    tags: Mapped[Optional[list]] = Column(JSON, nullable=True, comment="Free-form tags")
    created_by: Mapped[Optional[str]] = Column(String, nullable=True, comment="Email of the admin who created it")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    # Retrieval usage signal: how often the agent has actually retrieved this
    # document, and when last. Foundation for a "Usage" column in the catalog UI
    # and for surfacing stale/unused docs to reviewers. Only agent retrievals
    # increment these — admin browsing/search in the UI does not.
    retrieval_count: Mapped[int] = Column(
        Integer, nullable=False, default=0, server_default="0",
        comment="Times the agent has retrieved this document",
    )
    last_retrieved_at: Mapped[Optional[datetime]] = Column(
        DateTime, nullable=True, comment="When the agent last retrieved this document",
    )
    # Ownership + freshness review. ``updated_at`` moves on any row write, so
    # staleness is measured from ``content_updated_at`` (title/body/link edits
    # only) or ``last_reviewed_at`` (an owner confirmed it is still accurate),
    # whichever is later. See app/services/context_catalog_staleness.py.
    owner: Mapped[Optional[str]] = Column(
        String, nullable=True, comment="Email of the person accountable for keeping this document current",
    )
    content_updated_at: Mapped[Optional[datetime]] = Column(
        DateTime, nullable=True, comment="When the title, body or link last changed",
    )
    last_reviewed_at: Mapped[Optional[datetime]] = Column(
        DateTime, nullable=True, comment="When someone last marked the document reviewed without editing it",
    )
    last_reviewed_by: Mapped[Optional[str]] = Column(
        String, nullable=True, comment="Email of whoever last marked the document reviewed",
    )
    stale_notified_at: Mapped[Optional[datetime]] = Column(
        DateTime, nullable=True, comment="When a stale-document reminder last mentioned this document",
    )


class ContextChunkModel(Base):
    """A retrievable slice of a document's body.

    ``domain_id`` is denormalized off the parent document so retrieval can
    filter by domain without a join.
    """

    __tablename__ = "context_chunks"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the chunk")
    document_id: Mapped[str] = Column(
        String, ForeignKey("context_documents.id"), nullable=False, index=True,
        comment="Owning document id",
    )
    domain_id: Mapped[str] = Column(String, nullable=False, index=True, comment="Denormalized owning domain id")
    chunk_index: Mapped[int] = Column(Integer, nullable=False, default=0, comment="Order of the chunk within the document")
    content: Mapped[str] = Column(Text, nullable=False, comment="Plain text/markdown content of the chunk")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)


class ContextDocumentReviewModel(Base):
    """One AI review run of a document: grades, recommendations, owner
    suggestions and a proposed rewrite. Suggestions only; nothing here changes
    the document until an admin applies it. The newest row per document is the
    one shown. See app/services/context_catalog_review/.
    """

    __tablename__ = "context_document_reviews"

    id: Mapped[str] = Column(String, primary_key=True, comment="Unique UUID for the review run")
    document_id: Mapped[str] = Column(
        String, ForeignKey("context_documents.id"), nullable=False, index=True,
        comment="Reviewed document id",
    )
    batch_id: Mapped[Optional[str]] = Column(
        String, nullable=True, index=True, comment="Review all run this belongs to; null for a single review",
    )
    status: Mapped[str] = Column(
        String, nullable=False, default="queued", index=True,
        comment="queued | running | completed | failed",
    )
    requested_by: Mapped[Optional[str]] = Column(String, nullable=True, comment="Email of the admin who asked")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    content_hash: Mapped[Optional[str]] = Column(
        String, nullable=True, comment="Hash of the title + body reviewed, to spot later edits",
    )
    model: Mapped[Optional[str]] = Column(String, nullable=True, comment="Model that wrote the review")
    overall_score: Mapped[Optional[int]] = Column(Integer, nullable=True, comment="0-100, from the metric scores")
    grade: Mapped[Optional[str]] = Column(String, nullable=True, comment="A-F, from overall_score")
    scores: Mapped[Optional[list]] = Column(JSON, nullable=True, comment="[{metric, score 1-5, rationale}]")
    summary: Mapped[Optional[str]] = Column(Text, nullable=True)
    recommendations: Mapped[Optional[list]] = Column(
        JSON, nullable=True, comment="[{metric, priority, issue, suggestion, excerpt}]",
    )
    suggested_owners: Mapped[Optional[list]] = Column(
        JSON, nullable=True, comment="[{name, email, reason, source, confidence}]",
    )
    related_sources: Mapped[Optional[list]] = Column(
        JSON, nullable=True, comment="[{title, url, kind, relation, note}]",
    )
    proposed_title: Mapped[Optional[str]] = Column(String, nullable=True)
    proposed_body_markdown: Mapped[Optional[str]] = Column(Text, nullable=True)
    proposed_tags: Mapped[Optional[list]] = Column(JSON, nullable=True)
    rewrite_summary: Mapped[Optional[str]] = Column(Text, nullable=True, comment="What the proposed rewrite changes")
    confluence_status: Mapped[Optional[str]] = Column(
        String, nullable=True, comment="available | not_configured | unavailable",
    )
    confluence_note: Mapped[Optional[str]] = Column(Text, nullable=True, comment="Why Confluence wasn't used")
    tool_calls: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    forced: Mapped[bool] = Column(
        Boolean, nullable=False, default=False, server_default="0",
        comment="The tool-round or time budget ran out before the reviewer finished",
    )
    error: Mapped[Optional[str]] = Column(Text, nullable=True)
    applied_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True, comment="When the rewrite was applied")
    applied_by: Mapped[Optional[str]] = Column(String, nullable=True)
