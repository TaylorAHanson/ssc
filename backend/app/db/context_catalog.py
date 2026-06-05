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

from sqlalchemy import Column, String, DateTime, Text, Integer, JSON, ForeignKey
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
