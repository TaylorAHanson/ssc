"""Semantic Context Catalog search: hybrid ranking, the indexer, and the keyword fallback.

The Postgres tests run against a real bundled PostgreSQL with pgvector (``pixeltable-pgserver``,
``backend/requirements-dev.txt``) and a deterministic fake embedder, so no test reaches the network.
They are skipped where that package isn't installed; the SQLite fallback tests always run.
"""
import hashlib
import re
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.db.context_catalog import (
    ContextChunkModel,
    ContextDocumentModel,
    ContextDocumentReviewModel,
    ContextDomainModel,
)
from app.services import context_search_index as idx
from app.services import embeddings
from app.services.context_catalog_service import ContextCatalogService

DIMS = 1024
MODEL = "system.ai.gte-large-en"
_CATALOG_TABLES = [
    ContextDomainModel.__table__,
    ContextDocumentModel.__table__,
    ContextChunkModel.__table__,
    ContextDocumentReviewModel.__table__,
]

# A passage and a query share a "concept" when they mention any word of it, with no word in common
# needed: that is what a paraphrase looks like to a real embedding model.
CONCEPTS = {
    "money": ("budget", "spend", "spending", "cost", "costs", "money", "finance", "dollars", "expensive", "price"),
    "outage": ("outage", "incident", "pager", "paged", "downtime", "rollback"),
    "pets": ("dog", "dogs", "puppy", "cat", "cats", "pet", "pets"),
    "access": ("access", "permission", "permissions", "entitlement", "grant"),
}


def fake_vector(body: str, dims: int = DIMS) -> list:
    words = re.findall(r"[a-z0-9]+", body.lower())
    v = [0.0] * dims
    for i, kws in enumerate(CONCEPTS.values()):
        if any(w in kws for w in words):
            v[i] = 1.0
    for w in words:
        v[16 + int(hashlib.md5(w.encode()).hexdigest(), 16) % (dims - 16)] += 0.08
    if not any(v):
        v[dims - 1] = 1.0
    return v


class FakeEmbeddings:
    """Factory + call log. Set ``error``, ``dims`` or ``reject`` to simulate endpoint behaviour."""

    def __init__(self):
        self.calls = []  # [(model, [texts])]
        self.error = None
        self.dims = DIMS
        self.reject = None  # a substring that makes the endpoint refuse the batch (HTTP 400)

    def factory(self, model):
        return _Fake(model, self)

    def inputs(self, model=None):
        return [t for m, batch in self.calls for t in batch if model is None or m == model]

    def clear(self):
        self.calls.clear()


class _Fake:
    def __init__(self, model, owner):
        self.model = model
        self.owner = owner

    def embed(self, texts, *, timeout):
        self.owner.calls.append((self.model, list(texts)))
        if self.owner.error:
            raise self.owner.error
        if self.owner.reject and any(self.owner.reject in t for t in texts):
            raise embeddings.EmbeddingError("refused", status=400, per_input=True)
        return [fake_vector(t, self.owner.dims) for t in texts]


# --------------------------------------------------------------------------- fixtures

@pytest.fixture
def fake(_no_live_embeddings, monkeypatch):
    """The fake embedder, installed, with semantic search on and a clean indexer state."""
    owner = FakeEmbeddings()
    embeddings.set_embedder_factory(owner.factory)
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_SEMANTIC_SEARCH", True)
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_EMBEDDING_MODEL", MODEL)
    idx.reset_state()
    idx.set_capabilities(None)
    yield owner
    idx.reset_state()
    idx.set_capabilities(None)


@pytest.fixture(scope="module")
def pg_server(tmp_path_factory):
    pgserver = pytest.importorskip("pixeltable_pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="delete")
    yield server
    server.cleanup()


def _engine_for(uri, database=None):
    url = make_url(uri)
    if database:
        url = url.set(database=database)
    eng = create_engine(url, connect_args={"options": "-csearch_path=selfservice,public -ctimezone=UTC"},
                        pool_pre_ping=True)
    with eng.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS selfservice"))
    return eng


@pytest.fixture(scope="module")
def pg_engine(pg_server):
    eng = _engine_for(pg_server.get_uri())
    Base.metadata.create_all(eng, tables=_CATALOG_TABLES)
    caps = idx.ensure_capabilities(eng)
    assert caps.vector, caps.note
    yield eng
    eng.dispose()


@pytest.fixture
def db(pg_engine, fake):
    session = sessionmaker(bind=pg_engine)()
    yield session
    session.rollback()
    session.close()
    with pg_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE context_chunk_vectors, context_chunks, context_document_reviews, context_documents, "
            "context_domains CASCADE"))


def _doc(db, title, body, domain=None, status="published"):
    domain = domain or ContextCatalogService.create_domain(db, name=f"Domain for {title}")
    return ContextCatalogService.create_document(
        db, domain_id=domain.id, title=title, body_markdown=body, status=status)


def _titles(results):
    return [r["document_title"] for r in results]


def _index(db):
    idx.reset_state()
    return idx.pass_once(db)


# --------------------------------------------------------------------------- hybrid search (Postgres)

def test_semantic_finds_a_paraphrase_that_shares_no_words(db, fake):
    _doc(db, "Tooling allowance", "The finance team approves every purchase over five hundred dollars.")
    _doc(db, "Team offsites", "Pick a venue, book travel and send the agenda.")
    assert ContextCatalogService.search(db, "how expensive can tools get") == []  # words alone find nothing
    assert _index(db) > 0

    results = ContextCatalogService.search(db, "how expensive can tools get")
    assert _titles(results) == ["Tooling allowance"]
    assert results[0]["match"] == "semantic"
    assert results[0]["content"].startswith("Tooling allowance")


def test_a_chunk_found_by_both_legs_is_marked_both_and_ranks_first(db, fake):
    _doc(db, "Budget rules", "Every budget needs an owner and a quarterly review of money spent.")
    _doc(db, "Pet policy", "Dogs are welcome on Fridays.")
    _index(db)

    results = ContextCatalogService.search(db, "budget")
    assert _titles(results)[0] == "Budget rules"
    assert results[0]["match"] == "both"
    assert "Pet policy" not in _titles(results)


def test_a_nonsense_query_matches_nothing(db, fake):
    _doc(db, "Budget rules", "Every budget needs an owner.")
    _index(db)
    assert ContextCatalogService.search(db, "zzqx blorp wibble") == []


def test_unpublished_documents_are_never_returned(db, fake):
    doc = _doc(db, "Tooling allowance", "The finance team approves every purchase over five hundred dollars.")
    _index(db)
    assert _titles(ContextCatalogService.search(db, "how expensive can tools get")) == ["Tooling allowance"]

    ContextCatalogService.update_document(db, doc.id, status="draft")
    assert ContextCatalogService.search(db, "how expensive can tools get") == []


def test_domain_filter_applies_to_the_semantic_leg_and_includes_subdomains(db, fake):
    root = ContextCatalogService.create_domain(db, name="Finance")
    child = ContextCatalogService.create_domain(db, name="Procurement", parent_id=root.id)
    other = ContextCatalogService.create_domain(db, name="People")
    _doc(db, "Purchasing", "Approve every purchase over five hundred dollars.", domain=child)
    _doc(db, "Hiring", "Approve every salary offer with the finance partner.", domain=other)
    _index(db)

    everything = ContextCatalogService.search(db, "how expensive can tools get")
    assert sorted(_titles(everything)) == ["Hiring", "Purchasing"]
    scoped = ContextCatalogService.search(db, "how expensive can tools get", domain_slug=root.slug)
    assert _titles(scoped) == ["Purchasing"]
    assert ContextCatalogService.search(db, "how expensive can tools get", domain_slug="no-such-domain") == []


def test_semantic_off_never_calls_the_endpoint(db, fake, monkeypatch):
    _doc(db, "Budget rules", "Every budget needs an owner.")
    _index(db)
    fake.clear()
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_SEMANTIC_SEARCH", False)

    results = ContextCatalogService.search(db, "budget")
    assert [r["match"] for r in results] == ["keyword"]
    assert fake.calls == []
    assert idx.status(db)["mode"] == "keyword"


# --------------------------------------------------------------------------- fallback when the endpoint fails

def test_endpoint_failure_falls_back_to_keywords_and_stops_calling_it(db, fake):
    _doc(db, "Budget rules", "Every budget needs an owner.")
    _index(db)
    fake.clear()
    fake.error = embeddings.EmbeddingError("down", status=503)

    first = ContextCatalogService.search(db, "budget owner")
    assert _titles(first) == ["Budget rules"] and first[0]["match"] == "keyword"
    assert len(fake.calls) == 1

    ContextCatalogService.search(db, "budget rules")  # a different query inside the 30 s window
    assert len(fake.calls) == 1, "a failing endpoint must not be called again until the breaker resets"

    fake.error = None
    embeddings.reset_breaker()
    assert ContextCatalogService.search(db, "budget rules")[0]["match"] in ("both", "semantic")


def test_a_slow_or_broken_endpoint_never_raises_out_of_search(db, fake):
    _doc(db, "Budget rules", "Every budget needs an owner.")
    _index(db)
    fake.error = RuntimeError("boom")
    assert _titles(ContextCatalogService.search(db, "budget")) == ["Budget rules"]


def test_a_database_error_in_the_semantic_query_leaves_the_session_usable(db, fake, monkeypatch):
    _doc(db, "Budget rules", "Every budget needs an owner.")
    _index(db)
    # Pretend the vector type lives in a schema that doesn't exist: the semantic SQL fails inside its savepoint.
    monkeypatch.setattr(idx, "capabilities", lambda _db: idx.Capabilities(vector=True, vector_schema="nope"))
    results = ContextCatalogService.search(db, "budget")
    assert _titles(results) == ["Budget rules"] and results[0]["match"] == "keyword"
    assert db.query(ContextDocumentModel).count() == 1  # the same session still works


# --------------------------------------------------------------------------- the indexer

def test_an_edit_re_embeds_only_the_passages_that_changed(db, fake, monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_CHUNK_SIZE", 120)
    paragraphs = [
        "First section explains how invoices are approved by the finance partner every month.",
        "Second section explains how incidents are paged to the on call engineer after midnight.",
        "Third section explains how pets are welcome in the office on Fridays only.",
    ]
    doc = _doc(db, "Handbook", "\n\n".join(paragraphs))
    assert _index(db) >= 3
    fake.clear()

    paragraphs[2] = "Third section explains how access requests are granted by the data owner."
    ContextCatalogService.update_document(db, doc.id, body_markdown="\n\n".join(paragraphs))
    assert _index(db) == 1
    (only,) = fake.inputs()
    assert "access requests" in only
    assert not any("invoices" in t or "incidents" in t for t in fake.inputs())


def test_changing_the_model_re_embeds_everything_and_cleans_up_the_old_vectors(db, fake, monkeypatch):
    _doc(db, "Budget rules", "Every budget needs an owner.")
    _index(db)
    count = db.execute(text("SELECT count(*) FROM context_chunk_vectors")).scalar()
    assert count >= 1
    fake.clear()

    monkeypatch.setattr(settings, "CONTEXT_CATALOG_EMBEDDING_MODEL", "system.ai.bge-large-en")
    assert _index(db) == count
    assert {m for m, _ in fake.calls} == {"system.ai.bge-large-en"}

    assert _index(db) == 0  # nothing pending: garbage collection runs
    assert db.execute(text("SELECT DISTINCT model FROM context_chunk_vectors")).scalars().all() == [
        "system.ai.bge-large-en"]


def test_a_wrong_sized_model_is_reported_and_writes_nothing(db, fake):
    _doc(db, "Budget rules", "Every budget needs an owner.")
    fake.dims = 512

    assert _index(db) == 0
    assert db.execute(text("SELECT count(*) FROM context_chunk_vectors")).scalar() == 0
    assert "1024" in idx.status(db)["lastError"]
    assert _titles(ContextCatalogService.search(db, "budget")) == ["Budget rules"]  # words still work


def test_an_endpoint_outage_backs_off_instead_of_retrying_every_cycle(db, fake):
    _doc(db, "Budget rules", "Every budget needs an owner.")
    idx.reset_state()
    fake.error = embeddings.EmbeddingError("The embedding endpoint is busy; indexing will retry.", status=429)

    assert idx.pass_once(db) == 0
    assert len(fake.calls) == 1
    assert idx.pass_once(db) == 0
    assert len(fake.calls) == 1, "inside the back-off window the endpoint is left alone"
    assert idx.status(db)["lastError"].startswith("The embedding endpoint is busy")

    fake.error = None
    idx._state.retry_at = time.monotonic() - 1  # the back-off has elapsed
    assert idx.pass_once(db) >= 1
    assert idx.status(db)["lastError"] is None


def test_one_refused_passage_does_not_block_the_rest_and_is_not_retried(db, fake):
    _doc(db, "Poison", "This passage contains the word poison which the endpoint refuses.")
    _doc(db, "Budget rules", "Every budget needs an owner.")
    idx.reset_state()

    assert idx.pass_once(db) >= 1
    assert "Budget rules" in _titles(ContextCatalogService.search(db, "budget"))
    assert _titles(ContextCatalogService.search(db, "poison")) == ["Poison"]  # still found by words
    calls_after_first_pass = len(fake.calls)

    fake.reject = "poison"
    idx._state.next_run_at = 0.0
    assert idx.pass_once(db) == 0
    assert len(fake.calls) == calls_after_first_pass, "a refused passage is not re-sent"


def test_a_model_that_refuses_every_passage_backs_off_instead_of_writing_them_off(db, fake):
    """E.g. a chat model chosen by mistake: the endpoint answers HTTP 400 for every input."""
    _doc(db, "Policy one", "The policy covers laptops.")
    _doc(db, "Policy two", "The policy covers phones.")
    fake.reject = "policy"
    idx.reset_state()

    assert idx.pass_once(db) == 0
    assert "embedding model" in idx.status(db)["lastError"]
    assert not idx._state.rejected, "no passage may be written off for a model-level refusal"

    fake.reject = None
    idx._state.retry_at = time.monotonic() - 1  # the back-off has elapsed and the setting was fixed
    assert idx.pass_once(db) >= 2
    assert idx.status(db)["indexed"] == idx.status(db)["total"]


def test_chunks_without_a_hash_are_hashed_then_embedded(db, fake):
    doc = _doc(db, "Budget rules", "Every budget needs an owner.")
    db.query(ContextChunkModel).update({ContextChunkModel.content_hash: None})
    db.commit()

    assert _index(db) >= 1
    assert db.query(ContextChunkModel).filter(ContextChunkModel.content_hash.is_(None)).count() == 0
    assert doc.id  # sanity: the document survived


def test_status_reports_index_progress(db, fake):
    _doc(db, "Budget rules", "Every budget needs an owner.")
    before = idx.status(db)
    assert before["mode"] == "semantic" and before["indexed"] == 0 and before["pending"] == before["total"] >= 1

    _index(db)
    after = idx.status(db)
    assert after["indexed"] == after["total"] and after["pending"] == 0
    assert after["model"] == MODEL and after["reason"] is None


def test_setup_is_idempotent_and_finds_the_extension_schema(pg_engine, fake):
    first = idx.ensure_capabilities(pg_engine)
    second = idx.ensure_capabilities(pg_engine)
    assert first == second and second.vector and second.vector_schema == "selfservice"


def test_works_when_the_vector_extension_lives_outside_the_app_schema(pg_server, fake):
    """Lakebase may have pgvector in `public` while the app's tables are in their own schema."""
    admin = create_engine(pg_server.get_uri(), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("CREATE DATABASE alt_schema"))
    eng = _engine_for(pg_server.get_uri(), database="alt_schema")
    try:
        with eng.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
            conn.execute(text("CREATE EXTENSION vector SCHEMA public"))
        Base.metadata.create_all(eng, tables=_CATALOG_TABLES)
        caps = idx.ensure_capabilities(eng)
        assert caps.vector and caps.vector_schema == "public"

        session = sessionmaker(bind=eng)()
        try:
            _doc(session, "Tooling allowance", "The finance team approves every purchase over five hundred dollars.")
            _index(session)
            assert _titles(ContextCatalogService.search(session, "how expensive can tools get")) == [
                "Tooling allowance"]
        finally:
            session.close()
    finally:
        eng.dispose()
        admin.dispose()


# --------------------------------------------------------------------------- SQLite: the keyword-only fallback

def test_on_sqlite_search_is_keyword_only_and_never_touches_the_endpoint(db_session, fake):
    domain = ContextCatalogService.create_domain(db_session, name="Finance")
    ContextCatalogService.create_document(
        db_session, domain_id=domain.id, title="Tooling allowance",
        body_markdown="The finance team approves every purchase over five hundred dollars.")

    assert idx.capabilities(db_session) == idx.KEYWORD_ONLY
    results = ContextCatalogService.search(db_session, "purchase")
    assert _titles(results) == ["Tooling allowance"] and results[0]["match"] == "keyword"
    # A paraphrase that shares no words finds nothing without the semantic leg, and never asks for one.
    assert ContextCatalogService.search(db_session, "how expensive can tools get") == []
    assert fake.calls == []


def test_on_sqlite_the_indexer_is_a_no_op_and_status_says_why(db_session, fake):
    domain = ContextCatalogService.create_domain(db_session, name="Finance")
    ContextCatalogService.create_document(
        db_session, domain_id=domain.id, title="Budget rules", body_markdown="Every budget needs an owner.")

    assert idx.pass_once(db_session) == 0
    status = idx.status(db_session)
    assert status["mode"] == "keyword" and status["semantic"] is False
    assert "pgvector" in status["reason"]
    assert fake.calls == []


def test_every_new_chunk_carries_its_vector_key(db_session):
    domain = ContextCatalogService.create_domain(db_session, name="Finance")
    doc = ContextCatalogService.create_document(
        db_session, domain_id=domain.id, title="Budget rules", body_markdown="Every budget needs an owner.")
    chunks = db_session.query(ContextChunkModel).filter(ContextChunkModel.document_id == doc.id).all()
    assert chunks and all(c.content_hash for c in chunks)
    assert chunks[0].content_hash == idx.content_hash("Budget rules", 0, chunks[0].content)


def test_embed_text_gives_later_chunks_their_document_title():
    assert idx.embed_text("Handbook", 0, "Handbook\n\nIntro") == "Handbook\n\nIntro"
    assert idx.embed_text("Handbook", 2, "Third part") == "Handbook\n\nThird part"
    assert idx.content_hash("A", 1, "x") != idx.content_hash("B", 1, "x")


def test_vector_checks_reject_bad_endpoint_answers():
    good = [[0.1] * DIMS]
    assert embeddings.check_vectors(good, 1) == good
    for bad in ([[0.1] * 3], [[0.1] * DIMS, [0.1] * DIMS], "nope", [[float("nan")] + [0.1] * (DIMS - 1)]):
        with pytest.raises(embeddings.EmbeddingError):
            embeddings.check_vectors(bad, 1)
    assert embeddings.vector_literal([1.0, 0.5]) == "[1,0.5]"
    assert embeddings.valid_model(MODEL) and embeddings.valid_model("my-custom-endpoint")
    assert not embeddings.valid_model("bad name!") and not embeddings.valid_model("")
