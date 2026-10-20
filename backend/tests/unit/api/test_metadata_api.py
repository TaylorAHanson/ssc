"""The Metadata Manager API (``/api/v1/metadata``, ADR-0006): the feature gate, browse and
admin checks, the error bodies, the change source, idempotency keys and notify scope.

The services are stubbed here; their behaviour has its own tests. Each test gets a
private in-memory database.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db  # noqa: F401  (registers every model on Base.metadata)
from app.api import deps
from app.api.identity import forwarded_identity
from app.api.v1 import metadata as metadata_api
from app.core.config import _yaml_config, settings
from app.db.base import Base
from app.db.session import get_db
from app.models.user import User
from app.services.metadata import authz, changes, reconciler
from app.services.metadata.errors import ChangeBlocked, Forbidden, NotFound, VersionConflict
from app.services.metadata.types import (
    Actor,
    ChangePreview,
    ChangeResult,
    Finding,
    PrincipalScope,
    ReconcileResult,
    VersionConflictItem,
)

BASE = "/api/v1/metadata"
APP_ID = "8c1f6a2e-1234-4cde-9abc-0123456789ab"


def _user(email, roles=(), actor_type="user"):
    return User(id=email, email=email, full_name=email, roles=list(roles), entitlements=[email],
                actor_type=actor_type, application_id=email if actor_type == "service_principal" else None)


ADMIN = _user("admin@example.com", roles=["Platform Admin"])
OWNER = _user("owner@example.com")
NOBODY = _user("nobody@example.com")
PIPELINE = _user(APP_ID, actor_type="service_principal")
USERS = {u.email: u for u in (ADMIN, OWNER, NOBODY, PIPELINE)}

ACTORS = {
    ADMIN.email: Actor(id=ADMIN.email, email=ADMIN.email, roles=ADMIN.roles, is_admin=True),
    OWNER.email: Actor(id=OWNER.email, email=OWNER.email),
    NOBODY.email: Actor(id=NOBODY.email, email=NOBODY.email),
    APP_ID: Actor(id=APP_ID, actor_type="service_principal",
                  principal=PrincipalScope(application_id=APP_ID, scopes=["sales_*"])),
}

BODY = {"ops": [{"op": "set_tags", "name": "sales_dev.crm.customers", "tags": {"dataset": "crm"}}],
        "reason": "Label the CRM tables"}


@pytest.fixture(autouse=True)
def _feature(monkeypatch):
    monkeypatch.setitem(_yaml_config.setdefault("features", {}), "metadata_v2", True)
    monkeypatch.setattr(authz, "actor_from_user", lambda db, user: ACTORS[user.id])
    monkeypatch.setattr(authz, "can_browse", lambda actor: actor.id != NOBODY.email)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def api(db):
    """``api(user, **headers)``: a client signed in as ``user``."""
    app = FastAPI()
    app.include_router(metadata_api.router, prefix=BASE)

    def current_user(request: Request) -> User:
        return USERS[request.headers["x-test-user"]]

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[deps.get_current_user_or_principal] = current_user
    clients = []

    def client_for(user, **headers):
        client = TestClient(app, headers={"x-test-user": user.email, **headers})
        clients.append(client)
        return client

    yield client_for
    for client in clients:
        client.close()


def _preview(**kw) -> ChangePreview:
    return ChangePreview(**kw)


@pytest.fixture
def submitted(monkeypatch):
    seen = {}

    def submit(db, ask, actor, *, source):
        seen.update(ask=ask, actor=actor, source=source)
        return ChangeResult(status="committed", preview=_preview(direct_count=1))

    monkeypatch.setattr(changes, "submit", submit)
    return seen


# --- Feature gate, browse and admin ------------------------------------------------------


def test_every_route_is_hidden_when_the_feature_is_off(api, monkeypatch):
    monkeypatch.setitem(_yaml_config["features"], "metadata_v2", False)

    response = api(ADMIN).post(f"{BASE}/changes", json=BODY)

    assert response.status_code == 404
    assert response.json()["detail"] == "The Metadata Manager is turned off"


def test_a_role_that_cannot_browse_gets_403(api):
    response = api(NOBODY).get(f"{BASE}/object", params={"name": "sales_dev.crm.customers"})

    assert response.status_code == 403
    assert "browse" in response.json()["detail"]


def test_only_admins_start_reconciler_runs(api, monkeypatch):
    monkeypatch.setattr(reconciler, "run", lambda db, **kw: ReconcileResult(
        run_id="r1", trigger="manual", mode="plan", status="completed", actor=kw["actor"]))

    refused = api(OWNER).post(f"{BASE}/runs", json={"kind": "plan"})
    started = api(ADMIN).post(f"{BASE}/runs", json={"kind": "plan"})

    assert refused.status_code == 403
    assert started.status_code == 200
    assert started.json()["actor"] == ADMIN.email


def test_an_empty_queue_run_reports_skipped(api, monkeypatch):
    monkeypatch.setattr(reconciler, "drain_queue", lambda db, **kw: None)
    monkeypatch.setattr(reconciler, "current_mode", lambda: "enforce")

    response = api(ADMIN).post(f"{BASE}/runs", json={"kind": "queue"})

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"


# --- Error bodies --------------------------------------------------------------------------


def test_a_version_conflict_is_409_with_the_conflicts(api, monkeypatch):
    conflict = VersionConflictItem(fqn_key="sales_dev.crm.customers", name="sales_dev.crm.customers",
                                   key="dataset", expected_version=1, current_version=2, current_value="sales")

    def submit(*a, **k):
        raise VersionConflict([conflict])

    monkeypatch.setattr(changes, "submit", submit)

    response = api(OWNER).post(f"{BASE}/changes", json=BODY)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Reload" in detail["message"]
    assert detail["conflicts"][0]["current_version"] == 2


def test_a_blocked_change_is_422_with_the_preview(api, monkeypatch):
    preview = _preview(blocked=True, findings=[Finding(code="key_unknown", severity="deny",
                                                       message="'colour' isn't a known key.")])

    def submit(*a, **k):
        raise ChangeBlocked(preview)

    monkeypatch.setattr(changes, "submit", submit)

    response = api(OWNER).post(f"{BASE}/changes", json=BODY)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"] == "'colour' isn't a known key."
    assert detail["preview"]["blocked"] is True


@pytest.mark.parametrize("exc, status", [(Forbidden("no"), 403), (NotFound("gone"), 404)])
def test_other_service_errors_keep_their_status_and_message(api, monkeypatch, exc, status):
    def preview(*a, **k):
        raise exc

    monkeypatch.setattr(changes, "preview", preview)

    response = api(OWNER).post(f"{BASE}/changes/preview", json=BODY)

    assert response.status_code == status
    assert response.json()["detail"] == exc.message


def test_an_invalid_search_is_400(api):
    response = api(OWNER).get(f"{BASE}/objects", params={"sort": "nonsense"})

    assert response.status_code == 400


# --- Source and idempotency ----------------------------------------------------------------


def test_a_browser_submission_is_recorded_as_ui(api, submitted):
    api(OWNER, **{"Sec-Fetch-Mode": "cors"}).post(f"{BASE}/changes", json=BODY)

    assert submitted["source"] == "ui"


def test_a_scripted_submission_is_recorded_as_api(api, submitted):
    api(OWNER).post(f"{BASE}/changes", json=BODY)

    assert submitted["source"] == "api"


def test_a_service_principal_is_always_api(api, submitted):
    api(PIPELINE, **{"Sec-Fetch-Mode": "cors"}).post(f"{BASE}/changes", json=BODY)

    assert submitted["source"] == "api"
    assert submitted["actor"].actor_type == "service_principal"


def test_the_idempotency_key_header_is_used_when_the_body_has_none(api, submitted):
    api(OWNER, **{"Idempotency-Key": "run-42"}).post(f"{BASE}/changes", json=BODY)

    assert submitted["ask"].idempotency_key == "run-42"


def test_the_body_idempotency_key_wins_over_the_header(api, submitted):
    api(OWNER, **{"Idempotency-Key": "header"}).post(f"{BASE}/changes", json={**BODY, "idempotency_key": "body"})

    assert submitted["ask"].idempotency_key == "body"


# --- Notify ----------------------------------------------------------------------------------


def test_notify_enqueues_valid_names_and_rejects_bad_ones(api, db):
    response = api(OWNER).post(f"{BASE}/reconcile", json={"names": [
        "Sales_Dev.CRM.Customers::Email", "sales_dev.crm.orders", "bad;name"]})

    assert response.status_code == 200
    body = response.json()
    assert body["enqueued"] == ["sales_dev.crm.customers", "sales_dev.crm.orders"]
    assert [r["name"] for r in body["rejected"]] == ["bad;name"]


def test_a_principal_may_only_notify_inside_its_scope(api, monkeypatch):
    monkeypatch.setattr(authz, "is_editable", lambda db, actor, key: (
        key.catalog.startswith("sales_"), [] if key.catalog.startswith("sales_") else ["Outside scope."]))

    response = api(PIPELINE).post(f"{BASE}/reconcile", json={"names": [
        "sales_dev.crm.customers", "finance_prd.gl.entries"]})

    body = response.json()
    assert body["enqueued"] == ["sales_dev.crm.customers"]
    assert body["rejected"] == [{"name": "finance_prd.gl.entries", "reason": "Outside scope."}]


# --- whoami ----------------------------------------------------------------------------------


def _request(headers):
    fwd = forwarded_identity({k.lower(): v for k, v in headers.items()})
    email = fwd["email"] or settings.MOCK_USER_EMAIL
    return SimpleNamespace(
        headers={k.lower(): v for k, v in headers.items()},
        state=SimpleNamespace(user={"email": email, "username": fwd["username"] or settings.MOCK_USER_NAME,
                                    "id": fwd["user_id"] or settings.MOCK_USER_ID, "forwarded": fwd},
                              token=None),
    )


def test_whoami_tells_an_unregistered_principal_what_to_register(db, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    who = metadata_api._whoami(db, _request({"X-Forwarded-User": APP_ID}))

    assert who.recognized_as == "unregistered_service_principal"
    assert who.actor.id == APP_ID
    assert who.can_browse is False
    assert APP_ID in who.message
    assert "x-forwarded-user" in who.headers_seen


def test_whoami_recognizes_a_person(db, monkeypatch):
    monkeypatch.setattr(deps, "get_current_user_or_principal", lambda request, db, override: OWNER)

    who = metadata_api._whoami(db, _request({"X-Forwarded-Email": OWNER.email}))

    assert who.recognized_as == "user"
    assert who.actor.id == OWNER.email
    assert who.can_browse is True


# --- Name patterns ------------------------------------------------------------------------


def _observe(db, *names):
    from app.db.metadata import MetadataObservedModel
    from app.services.metadata.types import ObjectKey

    for name in names:
        key = ObjectKey.parse(name)
        db.add(MetadataObservedModel(
            fqn_key=key.fqn_key, catalog=key.catalog, schema_name=key.schema_name, object_name=key.object_name,
            parent_key=key.parent().fqn_key, display_name=key.display, securable_type="table", tags={},
            present=True,
        ))
    db.commit()


def test_a_pattern_reaches_the_change_service_as_named_objects(api, db, submitted):
    _observe(db, "sales_dev.crm.customers", "sales_dev.crm.orders", "hr_dev.people.staff")
    body = {"ops": [{"op": "set_tags", "match": "sales_dev.crm.*", "tags": {"dataset": "crm"}}],
            "reason": "Label the CRM tables"}

    response = api(OWNER).post(f"{BASE}/changes", json=body)

    assert response.status_code == 200
    assert [o.name for o in submitted["ask"].ops] == ["sales_dev.crm.customers", "sales_dev.crm.orders"]
    assert response.json()["preview"]["findings"][0]["code"] == "pattern_matched"


def test_a_pattern_that_matches_nothing_is_a_400(api, db, submitted):
    body = {"ops": [{"op": "set_tags", "match": "nowhere.*.*", "tags": {"dataset": "crm"}}], "reason": "x"}

    response = api(OWNER).post(f"{BASE}/changes/preview", json=body)

    assert response.status_code == 400
    assert "No known object matches" in response.json()["detail"]


def test_an_op_with_both_name_and_match_is_rejected(api):
    body = {"ops": [{"op": "set_tags", "name": "a.b.c", "match": "a.b.*", "tags": {"k": "v"}}], "reason": "x"}

    assert api(OWNER).post(f"{BASE}/changes/preview", json=body).status_code == 422


# --- End to end, real services ------------------------------------------------------------


def test_a_bulk_change_round_trips_through_the_real_services(api, db):
    _observe(db, "sales_dev.crm.customers", "sales_dev.crm.orders")
    body = {"ops": [{"op": "set_tags", "match": "sales_dev.crm.*", "tags": {"domain": "crm", "pii": None}},
                    {"op": "set_tags", "name": "sales_dev.crm.leads", "tags": {"domain": "crm"}}],
            "reason": "Tags agreed in the data council"}
    client = api(ADMIN, **{"Idempotency-Key": "council-1"})

    preview = client.post(f"{BASE}/changes/preview", json=body).json()
    assert (preview["object_count"], preview["direct_count"], preview["blocked"]) == (3, 5, False)

    result = client.post(f"{BASE}/changes", json=body).json()
    assert result["status"] == "committed" and result["change_set"]["source"] == "api"
    assert client.post(f"{BASE}/changes", json=body).json()["replayed"] is True

    detail = client.get(f"{BASE}/object", params={"name": "sales_dev.crm.orders"}).json()
    assert detail["desired"]["tags"] == {"domain": "crm", "pii": None}
    leads = client.get(f"{BASE}/object", params={"name": "sales_dev.crm.leads"}).json()
    assert leads["desired"]["object_state"] == "pending"
    found = client.get(f"{BASE}/objects", params={"tag": "domain=crm"}).json()
    assert sorted(i["name"] for i in found["items"]) == [
        "sales_dev.crm.customers", "sales_dev.crm.leads", "sales_dev.crm.orders"]

    history = client.get(f"{BASE}/changes").json()
    assert history["items"][0]["id"] == result["change_set"]["id"]
