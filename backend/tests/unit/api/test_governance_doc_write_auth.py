"""Writes to shared governance documents, and the scans that run as the governance
service principal, are limited to Platform Admin and Governance Admin.

Covers ``POST /data-contracts``, ``POST /data-contracts/sync``,
``POST /data-contracts/{id}/check-policy``, ``POST /odps`` and ``POST /odps/draft``. Each test gets a
private in-memory database because the endpoints commit.
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db  # noqa: F401  (registers every model on Base.metadata)
from app.api.deps import get_current_user
from app.api.v1 import data_contracts as data_contracts_api
from app.api.v1 import odps as odps_api
from app.db import RequestModel
from app.db.base import Base
from app.db.data_contract import DataContractModel
from app.db.odps import OdpsModel
from app.db.session import get_db
from app.models.user import User

DATASET = "main.sales.orders"
CONTRACT_YAML = "apiVersion: v3.0.0\nkind: DataContract\nid: orders\n"
ODPS_YAML = "version: '4.1'\nproduct:\n  details:\n    en:\n      name: Orders\n"


def _user(email, roles=()):
    return User(id=email, email=email, full_name=email, roles=list(roles), is_active=True)


PLATFORM_ADMIN = _user("admin@example.com", roles=["Platform Admin"])
GOVERNANCE_ADMIN = _user("gov@example.com", roles=["Governance Admin"])
PLAIN_USER = _user("user@example.com", roles=["User"])
USERS = {u.email: u for u in (PLATFORM_ADMIN, GOVERNANCE_ADMIN, PLAIN_USER)}
ADMINS = [PLATFORM_ADMIN, GOVERNANCE_ADMIN]


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
    """``api(user)``: a client signed in as ``user``."""
    app = FastAPI()
    app.include_router(data_contracts_api.router, prefix="/data-contracts")
    app.include_router(odps_api.router, prefix="/odps")

    def current_user(request: Request) -> User:
        return USERS[request.headers["x-test-user"]]

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = current_user
    clients = {}

    def client_for(user):
        if user.email not in clients:
            clients[user.email] = TestClient(app, headers={"x-test-user": user.email})
        return clients[user.email]

    yield client_for
    for client in clients.values():
        client.close()


# --- POST /data-contracts -------------------------------------------------

def test_plain_user_cannot_save_a_data_contract(api, db):
    res = api(PLAIN_USER).post("/data-contracts", json={"dataset_id": DATASET, "yaml_content": CONTRACT_YAML})

    assert res.status_code == 403
    assert db.query(DataContractModel).count() == 0


@pytest.mark.parametrize("admin", ADMINS, ids=lambda u: u.roles[0])
def test_admin_saves_a_data_contract_and_is_recorded_as_author(api, db, admin):
    res = api(admin).post("/data-contracts", json={"dataset_id": DATASET, "yaml_content": CONTRACT_YAML})

    assert res.status_code == 200, res.text
    saved = db.query(DataContractModel).filter_by(dataset_id=DATASET, is_active=True).one()
    assert saved.created_by == admin.email


# --- POST /data-contracts/sync --------------------------------------------

def test_plain_user_cannot_trigger_a_contract_sync(api):
    with patch.object(data_contracts_api, "discover_dataset_groups") as discover:
        res = api(PLAIN_USER).post("/data-contracts/sync")

    assert res.status_code == 403
    discover.assert_not_called()


@pytest.mark.parametrize("admin", ADMINS, ids=lambda u: u.roles[0])
def test_admin_can_trigger_a_contract_sync(api, admin):
    with patch.object(data_contracts_api, "discover_dataset_groups", return_value={}) as discover:
        res = api(admin).post("/data-contracts/sync")

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "success"
    discover.assert_called_once()


# --- POST /data-contracts/{dataset_id}/check-policy -----------------------

def test_plain_user_cannot_start_a_policy_check(api, db):
    res = api(PLAIN_USER).post(f"/data-contracts/{DATASET}/check-policy")

    assert res.status_code == 403
    assert db.query(RequestModel).count() == 0


@pytest.mark.parametrize("admin", ADMINS, ids=lambda u: u.roles[0])
def test_admin_can_start_a_policy_check(api, db, admin):
    res = api(admin).post(f"/data-contracts/{DATASET}/check-policy")

    assert res.status_code == 200, res.text
    run = db.query(RequestModel).one()
    assert run.type == "enforcement_sentinel"
    assert run.requester_email == admin.email


# --- POST /odps -----------------------------------------------------------

def test_plain_user_cannot_save_an_odps_document(api, db):
    res = api(PLAIN_USER).post("/odps", json={"name": "Orders", "yaml_content": ODPS_YAML})

    assert res.status_code == 403
    assert db.query(OdpsModel).count() == 0


@pytest.mark.parametrize("admin", ADMINS, ids=lambda u: u.roles[0])
def test_admin_saves_an_odps_document(api, db, admin):
    res = api(admin).post("/odps", json={"name": "Orders", "yaml_content": ODPS_YAML})

    assert res.status_code == 200, res.text
    assert res.json()["created_by"] == admin.email
    assert db.query(OdpsModel).filter_by(name="Orders", is_active=True).count() == 1


# --- POST /odps/draft -----------------------------------------------------

def test_plain_user_cannot_draft_an_odps_document(api):
    with patch("app.tools.governance.draft_odps.draft_odps_document") as draft:
        res = api(PLAIN_USER).post(
            "/odps/draft", json={"name": "Orders", "dataset_ids": [DATASET], "openapi_urls": ["http://internal/"]},
        )

    assert res.status_code == 403
    draft._func.assert_not_called()
