"""Tests for Context Catalog export/import (env promotion)."""
import pytest

from app.db.context_catalog import ContextChunkModel, ContextDocumentModel
from app.services.context_catalog_service import (
    CONTEXT_BUNDLE_FORMAT,
    ContextCatalogService as Svc,
)


def _seed_catalog(db):
    parent = Svc.create_domain(db, name="GTM Operations", description="Top level")
    child = Svc.create_domain(db, name="SSA", parent_id=parent.id, primary_owner="a@corp.com")
    Svc.create_document(
        db, domain_id=child.id, title="Playbook",
        body_markdown="# Playbook\n\nThe lakehouse combines lake and warehouse.",
        status="published", tags=["gtm"],
    )
    Svc.create_document(
        db, domain_id=child.id, title="Draft notes",
        body_markdown="rough notes", status="draft",
    )
    return parent, child


def test_export_bundle_is_portable_and_nests_documents(db_session):
    parent, child = _seed_catalog(db_session)
    bundle = Svc.export_bundle(db_session)

    assert bundle["format"] == CONTEXT_BUNDLE_FORMAT
    by_slug = {d["slug"]: d for d in bundle["domains"]}
    assert parent.slug in by_slug and child.slug in by_slug
    # Parent is referenced by slug, not the env-specific id.
    assert by_slug[child.slug]["parent_slug"] == parent.slug
    assert by_slug[parent.slug]["parent_slug"] is None
    titles = {doc["title"] for doc in by_slug[child.slug]["documents"]}
    assert titles == {"Playbook", "Draft notes"}


def test_export_published_only_filters_docs(db_session):
    _, child = _seed_catalog(db_session)
    bundle = Svc.export_bundle(db_session, published_only=True)
    child_entry = next(d for d in bundle["domains"] if d["slug"] == child.slug)
    titles = {doc["title"] for doc in child_entry["documents"]}
    assert titles == {"Playbook"}  # the draft is excluded


def test_import_roundtrip_recreates_tree_and_chunks(db_session):
    _seed_catalog(db_session)
    bundle = Svc.export_bundle(db_session)

    # Wipe everything, then import the bundle back.
    db_session.query(ContextChunkModel).delete()
    db_session.query(ContextDocumentModel).delete()
    for d in Svc.list_domains(db_session):
        db_session.delete(d)
    db_session.commit()
    assert Svc.list_domains(db_session) == []

    report = Svc.import_bundle(db_session, bundle, created_by="importer@corp.com")
    assert len(report["domains"]["created"]) == 2
    assert "ssa/Playbook" in report["documents"]["created"]

    # Tree re-linked by slug.
    parent = Svc.get_domain_by_slug(db_session, "gtm-operations")
    child = Svc.get_domain_by_slug(db_session, "ssa")
    assert child.parent_id == parent.id

    # Published doc is searchable again (chunks rebuilt); draft is not.
    results = Svc.search(db_session, "lakehouse")
    assert any(r["document_title"] == "Playbook" for r in results)


def test_import_upserts_by_slug_and_title(db_session):
    _, child = _seed_catalog(db_session)
    bundle = Svc.export_bundle(db_session)
    # Mutate the exported Playbook body; re-import should update in place.
    for d in bundle["domains"]:
        for doc in d["documents"]:
            if doc["title"] == "Playbook":
                doc["body_markdown"] = "# Playbook\n\nUpdated content about governance."

    report = Svc.import_bundle(db_session, bundle, overwrite=True)
    assert f"{child.slug}/Playbook" in report["documents"]["updated"]

    docs = Svc.list_documents(db_session, child.id)
    playbooks = [d for d in docs if d.title == "Playbook"]
    assert len(playbooks) == 1  # updated, not duplicated
    assert "governance" in (playbooks[0].body_markdown or "")


def test_import_skips_when_overwrite_false(db_session):
    _, child = _seed_catalog(db_session)
    bundle = Svc.export_bundle(db_session)
    report = Svc.import_bundle(db_session, bundle, overwrite=False)
    assert child.slug in report["domains"]["skipped"]
    assert f"{child.slug}/Playbook" in report["documents"]["skipped"]


def test_import_rejects_bad_format(db_session):
    with pytest.raises(ValueError):
        Svc.import_bundle(db_session, {"format": "nope", "domains": []})
