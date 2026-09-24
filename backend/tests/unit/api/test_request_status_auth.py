"""GET /requests/{id}/status is limited to the requester and platform admins."""

import pytest
from fastapi import HTTPException

from app.api.v1.requests import get_request_status
from app.db import RequestModel
from app.models.user import User


def _user(email, roles=()):
    return User(id=email, email=email, full_name=email, roles=list(roles), is_active=True)


@pytest.fixture
def owned_request(db_session):
    db_session.add(RequestModel(
        id="req-s", type="t", title="t", status="pending",
        current_state="await_approval", requester_email="owner@example.com",
    ))
    db_session.commit()
    return "req-s"


def test_requester_can_read_status(db_session, owned_request):
    out = get_request_status(owned_request, current_user=_user("owner@example.com"), db=db_session)
    assert out["status"] == "pending"
    assert out["current_state"] == "await_approval"


def test_platform_admin_can_read_any_status(db_session, owned_request):
    admin = _user("admin@example.com", roles=["Platform Admin"])
    assert get_request_status(owned_request, current_user=admin, db=db_session)["status"] == "pending"


def test_other_user_is_forbidden(db_session, owned_request):
    with pytest.raises(HTTPException) as exc_info:
        get_request_status(owned_request, current_user=_user("someone@example.com"), db=db_session)
    assert exc_info.value.status_code == 403


def test_unknown_request_is_404(db_session):
    with pytest.raises(HTTPException) as exc_info:
        get_request_status("req-missing", current_user=_user("owner@example.com"), db=db_session)
    assert exc_info.value.status_code == 404
