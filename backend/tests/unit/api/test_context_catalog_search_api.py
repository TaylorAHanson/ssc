"""Context Catalog search over HTTP: results say how they matched, and admins can see the search mode."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.v1 import context_catalog
from app.db.base import Base
from app.db.session import get_db
from app.models.user import User
from app.services.context_catalog_service import ContextCatalogService


def _user(email, roles=()):
    return User(id=email, email=email, full_name=email, roles=list(roles), entitlements=[email], is_active=True)


ADMIN = _user("admin@example.com", roles=["Super Admin"])
READER = _user("reader@example.com")


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(context_catalog.router, prefix="/context")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    return TestClient(app), app


def test_search_results_say_how_they_matched(client, db):
    http, _ = client
    domain = ContextCatalogService.create_domain(db, name="Finance")
    ContextCatalogService.create_document(
        db, domain_id=domain.id, title="Tooling allowance",
        body_markdown="The finance team approves every purchase over five hundred dollars.")

    res = http.post("/context/search", json={"query": "purchase"})

    assert res.status_code == 200
    (hit,) = res.json()["results"]
    assert hit["document_title"] == "Tooling allowance"
    assert hit["match"] == "keyword"
    assert hit["chunk_id"]


def test_status_reports_keyword_mode_and_why_on_sqlite(client, db):
    http, _ = client
    domain = ContextCatalogService.create_domain(db, name="Finance")
    ContextCatalogService.create_document(db, domain_id=domain.id, title="A", body_markdown="Some text here.")

    res = http.get("/context/search/status")

    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "keyword" and body["semantic"] is False
    assert body["total"] >= 1 and body["indexed"] == 0
    assert "pgvector" in body["reason"]


def test_status_needs_the_manage_capability(client):
    http, app = client
    app.dependency_overrides[get_current_user] = lambda: READER
    assert http.get("/context/search/status").status_code == 403
