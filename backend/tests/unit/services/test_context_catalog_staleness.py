"""Context Catalog document owners, staleness and Teams reminder selection."""
from datetime import datetime, timedelta

import pytest

from app.core import config as cfg
from app.db.context_catalog import ContextDocumentModel
from app.providers.notifications import teams
from app.services import context_catalog_staleness as staleness
from app.services.context_catalog_service import ContextCatalogService as Svc

NOW = datetime(2026, 10, 26, 12, 0, 0)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_STALE_AFTER_DAYS", 90)
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_STALE_RENOTIFY_DAYS", 7)
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_TEAMS_WEBHOOK_URL", "https://hook.example/wf")
    monkeypatch.setattr(cfg.settings, "APP_BASE_URL", "https://app.example/")


@pytest.fixture
def posted(monkeypatch):
    """Capture Teams posts instead of sending them."""
    calls = []

    def fake_post(url, message, **_):
        calls.append((url, message))
        return teams.PostResult(ok=True, status_code=202)

    monkeypatch.setattr(teams, "post_message", fake_post)
    return calls


def _doc(db, domain, title, *, age_days, status="published", owner=None, reviewed_days=None):
    doc = Svc.create_document(db, domain_id=domain.id, title=title, body_markdown="body", status=status,
                              owner=owner)
    doc.content_updated_at = NOW - timedelta(days=age_days)
    doc.last_reviewed_at = NOW - timedelta(days=reviewed_days) if reviewed_days is not None else None
    doc.owner = owner
    db.commit()
    return doc


def _ids(docs):
    return {d.id for d in docs}


def test_only_published_docs_past_the_threshold_are_stale(db_session):
    domain = Svc.create_domain(db_session, name="Ops")
    old = _doc(db_session, domain, "Old", age_days=120)
    fresh = _doc(db_session, domain, "Fresh", age_days=10)
    _doc(db_session, domain, "Old draft", age_days=400, status="draft")

    stale = staleness.find_stale_documents(db_session, now=NOW)

    assert _ids(stale) == {old.id}
    assert stale[0].age_days == 120
    assert stale[0].url == (
        f"https://app.example/governance/context-catalog?domain={domain.id}&doc={old.id}"
    )
    assert not staleness.is_stale(fresh, now=NOW)


def test_a_recent_review_counts_as_activity(db_session):
    domain = Svc.create_domain(db_session, name="Ops")
    reviewed = _doc(db_session, domain, "Reviewed", age_days=200, reviewed_days=5)
    assert staleness.find_stale_documents(db_session, now=NOW) == []
    assert staleness.last_activity_at(reviewed) == NOW - timedelta(days=5)


def test_mark_reviewed_restarts_the_clock_without_touching_content(db_session):
    domain = Svc.create_domain(db_session, name="Ops")
    doc = _doc(db_session, domain, "Old", age_days=120)
    doc.stale_notified_at = NOW - timedelta(days=1)
    db_session.commit()
    content_ts = doc.content_updated_at

    staleness.mark_reviewed(db_session, doc.id, "reviewer@corp.com")

    refreshed = db_session.get(ContextDocumentModel, doc.id)
    assert refreshed.content_updated_at == content_ts
    assert refreshed.last_reviewed_by == "reviewer@corp.com"
    assert refreshed.stale_notified_at is None
    assert not staleness.is_stale(refreshed)


def test_retrieval_usage_does_not_move_updated_or_content_timestamps(db_session):
    domain = Svc.create_domain(db_session, name="Ops")
    doc = Svc.create_document(db_session, domain_id=domain.id, title="Lakehouse",
                              body_markdown="The lakehouse architecture.")
    old = NOW - timedelta(days=200)
    doc.updated_at = old
    doc.content_updated_at = old
    db_session.commit()

    Svc.search(db_session, "lakehouse", track_usage=True)
    db_session.expire_all()

    refreshed = db_session.get(ContextDocumentModel, doc.id)
    assert refreshed.retrieval_count == 1
    assert refreshed.updated_at == old
    assert refreshed.content_updated_at == old


def test_content_edits_move_content_updated_at_but_owner_edits_do_not(db_session):
    domain = Svc.create_domain(db_session, name="Ops")
    doc = _doc(db_session, domain, "Doc", age_days=120)
    doc.stale_notified_at = NOW
    db_session.commit()
    old = doc.content_updated_at

    Svc.update_document(db_session, doc.id, owner="New.Owner@Corp.com", status="published")
    refreshed = db_session.get(ContextDocumentModel, doc.id)
    assert refreshed.content_updated_at == old
    assert refreshed.owner == "new.owner@corp.com"

    Svc.update_document(db_session, doc.id, body_markdown="rewritten")
    refreshed = db_session.get(ContextDocumentModel, doc.id)
    assert refreshed.content_updated_at > old
    assert refreshed.stale_notified_at is None

    Svc.update_document(db_session, doc.id, owner="")
    assert db_session.get(ContextDocumentModel, doc.id).owner is None


def test_owner_defaults_to_creator_and_round_trips_through_a_bundle(db_session):
    domain = Svc.create_domain(db_session, name="Ops")
    doc = Svc.create_document(db_session, domain_id=domain.id, title="Doc", created_by="Creator@Corp.com")
    assert doc.owner == "creator@corp.com"
    Svc.update_document(db_session, doc.id, owner="owner@corp.com")

    bundle = Svc.export_bundle(db_session)
    exported = bundle["domains"][0]["documents"][0]
    assert exported["owner"] == "owner@corp.com"

    # Re-import over a doc whose owner changed locally: the bundle owner wins.
    Svc.update_document(db_session, doc.id, owner="someone.else@corp.com")
    Svc.import_bundle(db_session, bundle)
    assert db_session.get(ContextDocumentModel, doc.id).owner == "owner@corp.com"

    # A bundle without an owner leaves the local owner alone.
    exported.pop("owner")
    Svc.import_bundle(db_session, bundle)
    assert db_session.get(ContextDocumentModel, doc.id).owner == "owner@corp.com"


def test_owner_falls_back_to_the_domain_primary_owner_then_none(db_session):
    owned_domain = Svc.create_domain(db_session, name="Owned", primary_owner="Lead@Corp.com")
    named_domain = Svc.create_domain(db_session, name="Named", primary_owner="Platform Team")
    bare_domain = Svc.create_domain(db_session, name="Bare")
    own = _doc(db_session, owned_domain, "Has owner", age_days=100, owner="writer@corp.com")
    inherit = _doc(db_session, owned_domain, "Inherits", age_days=100)
    named = _doc(db_session, named_domain, "Named owner", age_days=100)
    orphan = _doc(db_session, bare_domain, "Orphan", age_days=100)

    stale = {d.id: d for d in staleness.find_stale_documents(db_session, now=NOW)}
    assert (stale[own.id].owner, stale[own.id].owner_source) == ("writer@corp.com", "document")
    assert (stale[inherit.id].owner, stale[inherit.id].owner_source) == ("lead@corp.com", "domain")
    assert stale[named.id].owner == "Platform Team"
    assert (stale[orphan.id].owner, stale[orphan.id].owner_source) == (None, None)

    groups = staleness.group_by_owner(list(stale.values()))
    # Mentionable owners first, then names that can't be mentioned, then no owner.
    assert [g.owner for g in groups] == ["lead@corp.com", "writer@corp.com", "Platform Team", None]
    assert groups[0].mention.id == "lead@corp.com"
    assert groups[2].mention is None and groups[3].mention is None


def test_reminders_respect_the_renotify_interval(db_session, posted):
    domain = Svc.create_domain(db_session, name="Ops")
    doc = _doc(db_session, domain, "Old", age_days=120, owner="owner@corp.com")

    first = staleness.run_stale_reminders(db_session, now=NOW)
    assert first["status"] == "sent"
    assert first["notified_documents"] == 1 and len(posted) == 1
    assert db_session.get(ContextDocumentModel, doc.id).stale_notified_at == NOW

    again = staleness.run_stale_reminders(db_session, now=NOW + timedelta(days=3))
    assert again["status"] == "nothing_due"
    assert again["skipped_recently_notified"] == 1
    assert len(posted) == 1

    later = staleness.run_stale_reminders(db_session, now=NOW + timedelta(days=8))
    assert later["status"] == "sent" and len(posted) == 2


def test_failed_post_leaves_documents_due(db_session, monkeypatch):
    domain = Svc.create_domain(db_session, name="Ops")
    doc = _doc(db_session, domain, "Old", age_days=120, owner="owner@corp.com")
    monkeypatch.setattr(teams, "post_message",
                        lambda url, message, **_: teams.PostResult(ok=False, status_code=400, error="HTTP 400"))

    report = staleness.run_stale_reminders(db_session, now=NOW)

    assert report["status"] == "failed" and report["errors"] == ["HTTP 400"]
    assert db_session.get(ContextDocumentModel, doc.id).stale_notified_at is None


def test_reminders_need_a_webhook(db_session, monkeypatch, posted):
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_TEAMS_WEBHOOK_URL", "")
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_TEAMS_WEBHOOK_SECRET_SCOPE", "")
    domain = Svc.create_domain(db_session, name="Ops")
    _doc(db_session, domain, "Old", age_days=120)

    assert staleness.run_stale_reminders(db_session, now=NOW)["status"] == "not_configured"
    assert posted == []


def test_document_payload_carries_owner_and_freshness(db_session):
    domain = Svc.create_domain(db_session, name="Ops")
    doc = _doc(db_session, domain, "Old", age_days=120, owner="owner@corp.com")
    data = Svc.document_to_dict(doc, include_body=False)
    assert data["owner"] == "owner@corp.com"
    assert data["is_stale"] is True
    assert data["stale_after_days"] == 90
    assert data["content_updated_at"] == doc.content_updated_at.isoformat()
