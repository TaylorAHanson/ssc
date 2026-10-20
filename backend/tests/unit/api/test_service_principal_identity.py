"""Service-principal callers (ADR-0006 D7).

A machine caller authenticates through the Apps proxy with an OAuth M2M token.
It arrives with identifiers but no email address, is recognized only when an
admin registered it in ``metadata_principals``, and only routes that opt in
accept it. Its groups never come from the app's own identity.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import deps
from app.api.identity import forwarded_identity, header_shape
from app.core.config import settings
from app.db.metadata import MetadataPrincipalModel

APP_ID = "8c1f6a2e-1234-4cde-9abc-0123456789ab"


def _request(headers):
    fwd = forwarded_identity({k.lower(): v for k, v in headers.items()})
    email = fwd["email"] or settings.MOCK_USER_EMAIL
    return SimpleNamespace(state=SimpleNamespace(
        user={"email": email, "username": fwd["username"] or settings.MOCK_USER_NAME,
              "id": fwd["user_id"] or settings.MOCK_USER_ID, "forwarded": fwd},
        token=None,
    ))


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")


@pytest.fixture
def no_scim(monkeypatch):
    calls = []

    def fake(email, obo=None):
        calls.append((email, obo))
        return [email, "pipeline-group"]

    monkeypatch.setattr(deps, "_get_user_entitlements", fake)
    return calls


@pytest.fixture
def registered(db_session):
    db_session.add(MetadataPrincipalModel(
        application_id=APP_ID, display_name="Ingest pipeline", enabled=True, scopes=["sales_*"],
    ))
    db_session.commit()
    return APP_ID


def test_a_registered_principal_is_recognized_on_routes_that_accept_one(production, no_scim, registered,
                                                                         db_session):
    request = _request({"X-Forwarded-User": APP_ID.upper()})

    user = deps.get_current_user_or_principal(request, db_session, None)

    assert user.actor_type == "service_principal"
    assert user.application_id == APP_ID
    assert user.email == APP_ID
    assert user.full_name == "Ingest pipeline"


def test_other_routes_refuse_a_service_principal(production, no_scim, registered, db_session):
    request = _request({"X-Forwarded-User": APP_ID})

    with pytest.raises(HTTPException) as caught:
        deps.get_current_user(request, db_session, None)

    assert caught.value.status_code == 403


def test_a_principal_is_matched_on_any_forwarded_identifier(production, no_scim, registered, db_session):
    request = _request({"X-Forwarded-Preferred-Username": APP_ID, "X-Forwarded-User": "12345"})

    user = deps.get_current_user_or_principal(request, db_session, None)

    assert user.application_id == APP_ID


def test_without_its_own_token_a_principal_gets_no_groups(production, no_scim, registered, db_session):
    """The SDK's default client is the app's identity; its groups must never be attributed to a caller."""
    user = deps.get_current_user_or_principal(_request({"X-Forwarded-User": APP_ID}), db_session, None)

    assert user.entitlements == [APP_ID]
    assert no_scim == []


def test_with_its_own_token_a_principal_gets_its_groups(production, no_scim, registered, db_session):
    request = _request({"X-Forwarded-User": APP_ID})
    request.state.token = "sp-token"

    user = deps.get_current_user_or_principal(request, db_session, None)

    assert "pipeline-group" in user.entitlements
    assert no_scim == [(APP_ID, "sp-token")]


def test_an_unregistered_caller_is_told_what_to_register(production, no_scim, db_session):
    with pytest.raises(HTTPException) as caught:
        deps.get_current_user_or_principal(_request({"X-Forwarded-User": APP_ID}), db_session, None)

    assert caught.value.status_code == 403
    assert APP_ID in caught.value.detail
    assert "System principals" in caught.value.detail


def test_a_disabled_principal_is_not_recognized(production, no_scim, db_session):
    db_session.add(MetadataPrincipalModel(application_id=APP_ID, display_name="off", enabled=False))
    db_session.commit()

    with pytest.raises(HTTPException) as caught:
        deps.get_current_user_or_principal(_request({"X-Forwarded-User": APP_ID}), db_session, None)

    assert caught.value.status_code == 403


def test_a_human_is_unaffected(production, no_scim, db_session):
    request = _request({"X-Forwarded-Email": "jane@corp.com", "X-Forwarded-User": "777"})

    user = deps.get_current_user(request, db_session, None)

    assert user.actor_type == "user"
    assert user.email == "jane@corp.com"
    assert "x-forwarded-email" in user.identity_headers


def test_the_header_shape_never_carries_values():
    fwd = forwarded_identity({"x-forwarded-user": APP_ID, "x-forwarded-email": "jane@corp.com",
                              "x-forwarded-access-token": "secret-token"})

    shape = header_shape(fwd)

    assert shape == {
        "headers": ["x-forwarded-email", "x-forwarded-user", "x-forwarded-access-token"],
        "email": "email", "user": "uuid", "username": None,
    }
    assert "secret-token" not in repr(shape)
