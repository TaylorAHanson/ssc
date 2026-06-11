"""Tests for the Context Catalog retrieval-usage signal."""
import uuid

from app.db.context_catalog import (
    ContextChunkModel,
    ContextDocumentModel,
    ContextDomainModel,
)
from app.services.context_catalog_service import ContextCatalogService


def _seed_doc(db, *, term: str = "lakehouse"):
    suffix = uuid.uuid4().hex[:8]
    domain = ContextDomainModel(
        id=f"dom-{suffix}",
        slug=f"dom-{suffix}",
        name=f"Domain {suffix}",
        domain_type="community",
    )
    doc = ContextDocumentModel(
        id=f"doc-{suffix}",
        domain_id=domain.id,
        title=f"Doc {suffix}",
        status="published",
        body_markdown=f"The {term} architecture.",
        retrieval_count=0,
    )
    chunk = ContextChunkModel(
        id=f"chk-{suffix}",
        document_id=doc.id,
        domain_id=domain.id,
        chunk_index=0,
        content=f"The {term} architecture combines lake and warehouse.",
    )
    db.add_all([domain, doc, chunk])
    db.flush()
    return doc


def test_track_usage_bumps_retrieval_counter(db_session):
    doc = _seed_doc(db_session, term="lakehouse")

    results = ContextCatalogService.search(db_session, "lakehouse", track_usage=True)
    assert any(r["document_id"] == doc.id for r in results)

    refreshed = db_session.get(ContextDocumentModel, doc.id)
    assert refreshed.retrieval_count == 1
    assert refreshed.last_retrieved_at is not None

    # A second tracked retrieval increments again.
    ContextCatalogService.search(db_session, "lakehouse", track_usage=True)
    assert db_session.get(ContextDocumentModel, doc.id).retrieval_count == 2


def test_search_without_tracking_leaves_counter_untouched(db_session):
    doc = _seed_doc(db_session, term="catalog")

    results = ContextCatalogService.search(db_session, "catalog")  # track_usage defaults False
    assert any(r["document_id"] == doc.id for r in results)

    assert db_session.get(ContextDocumentModel, doc.id).retrieval_count == 0
    assert db_session.get(ContextDocumentModel, doc.id).last_retrieved_at is None
