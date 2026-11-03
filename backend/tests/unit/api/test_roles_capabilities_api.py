"""Roles & Access: the capability grid, the current user's capabilities and who may edit mappings."""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.v1 import roles as roles_api
from app.core import capabilities
import app.db  # noqa: F401  (registers every model on Base.metadata)
from app.db.base import Base
from app.db.role_mapping import RoleMappingModel
from app.db.session import get_db
from app.models.user import User


def _user(email, roles):
    return User(id=email, email=email, full_name=email, roles=list(roles), is_active=True)


SUPER = _user("super@example.com", ["Super Admin"])
APP = _user("app@example.com", ["Application Admin"])
GOV = _user("gov@example.com", ["Governance Admin"])
OPS = _user("ops@example.com", ["Ops Admin"])
USERS = {u.email: u for u in (SUPER, APP, GOV, OPS)}


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        RoleMappingModel(external_role="owners", internal_role="Super Admin"),
        RoleMappingModel(external_role="app-team", internal_role="Platform Admin"),
        RoleMappingModel(external_role="ops-team", internal_role="Ops Admin"),
    ])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _current_user(request: Request) -> User:
    return USERS[request.headers["x-test-user"]]


def _client(db, user):
    app = FastAPI()
    app.include_router(roles_api.router, prefix="/roles")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = _current_user
    return TestClient(app, headers={"x-test-user": user.email})


def test_me_lists_the_callers_capabilities(db):
    body = _client(db, OPS).get("/roles/me").json()
    assert body["capabilities"] == capabilities.capabilities_for(["Ops Admin"])
    assert "requests.view_all" in body["capabilities"]


def test_grid_lists_every_role_and_capability(db):
    res = _client(db, APP).get("/roles/capabilities")
    assert res.status_code == 200
    grid = res.json()

    assert [r["label"] for r in grid["roles"]] == list(capabilities.ROLE_LABELS)
    mapped = {r["label"]: r["mapped_identities"] for r in grid["roles"]}
    assert mapped["Application Admin"] == ["app-team"]
    assert mapped["Super Admin"] == ["owners"]
    assert mapped["Ops Admin"] == ["ops-team"]
    assert [c["id"] for c in grid["capabilities"]] == [c.id for c in capabilities.all_capabilities()]
    allow = next(c for c in grid["capabilities"] if c["id"] == "allowlist.manage")
    assert allow["roles"] == ["Super Admin", "Governance Admin"]
    viewer = next(c for c in grid["capabilities"] if c["id"] == "access_rules.view")
    assert viewer["also_from_setting"]["key"] == "RBAC_VIEWER_ROLES"
    assert grid["can_manage_roles"] is False


def test_super_admin_may_manage_roles(db):
    assert _client(db, SUPER).get("/roles/capabilities").json()["can_manage_roles"] is True


@pytest.mark.parametrize("user", [GOV, OPS], ids=lambda u: u.roles[0])
def test_grid_and_mappings_need_roles_view(db, user):
    client = _client(db, user)
    assert client.get("/roles/capabilities").status_code == 403
    assert client.get("/roles/mapping").status_code == 403


def test_application_admin_reads_but_cannot_change_mappings(db):
    client = _client(db, APP)
    assert client.get("/roles/mapping").status_code == 200
    res = client.post("/roles/mapping", json={"external_role": "x", "internal_role": "Super Admin"})
    assert res.status_code == 403
    assert "Manage role mappings" in res.json()["detail"]


def test_super_admin_creates_mappings_with_canonical_role_names(db):
    res = _client(db, SUPER).post("/roles/mapping", json={"external_role": " new-team ", "internal_role": "platform_admin"})
    assert res.status_code == 200, res.text
    assert (res.json()["external_role"], res.json()["internal_role"]) == ("new-team", "Application Admin")
