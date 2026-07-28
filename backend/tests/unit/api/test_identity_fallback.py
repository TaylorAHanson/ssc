"""The mock-identity fallback in ``get_current_user`` must stay in dev.

When the proxy forwards no ``x-forwarded-email``, the auth middleware substitutes
``settings.MOCK_USER_EMAIL``, and ``get_current_user`` used to quietly resolve
that to the shared ``admin@example.com`` identity in *any* environment. That is a
cross-user hazard on two counts: ``role_mappings`` seeds
``admin@example.com -> Platform Admin``, so the caller is elevated without any dev
gate; and the per-user chat transcripts and cached agent context are keyed on the
email, so every unidentified caller would share one pool of both.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import deps
from app.core.config import settings


def _request(email=None, *, with_state=True):
    """A stand-in for the Request the middleware has already annotated."""
    if not with_state:
        return SimpleNamespace(state=SimpleNamespace())
    return SimpleNamespace(
        state=SimpleNamespace(user={"email": email, "username": email}, token=None)
    )


@pytest.fixture
def no_scim(monkeypatch):
    """Keep the SCIM lookup off the network for the identified-caller cases."""
    monkeypatch.setattr(deps, "_get_user_entitlements", lambda email, obo=None: [email])


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "stg"])
def test_an_unidentified_caller_is_rejected_outside_dev(environment, monkeypatch, db_session):
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)

    with pytest.raises(HTTPException) as caught:
        deps.get_current_user(_request(settings.MOCK_USER_EMAIL), db_session, None)

    assert caught.value.status_code == 401


def test_a_request_with_no_state_at_all_is_rejected_outside_dev(monkeypatch, db_session):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    with pytest.raises(HTTPException) as caught:
        deps.get_current_user(_request(with_state=False), db_session, None)

    assert caught.value.status_code == 401


def test_the_fallback_still_works_in_dev(monkeypatch, db_session):
    """Local development depends on this: there is no proxy to forward a header."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    user = deps.get_current_user(_request(settings.MOCK_USER_EMAIL), db_session, None)

    assert user.email == deps.MOCK_USER_EMAIL
    assert user.full_name == "System Admin"


@pytest.mark.parametrize("environment", ["production", "development"])
def test_a_real_forwarded_identity_is_never_collapsed(environment, monkeypatch, db_session, no_scim):
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)

    user = deps.get_current_user(_request("real.person@corp.com"), db_session, None)

    assert user.email == "real.person@corp.com"
    assert user.email != deps.MOCK_USER_EMAIL
