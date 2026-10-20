"""The groups a signed-in user carries (``deps._get_user_entitlements``).

The workspace's ``me()`` lists only groups assigned to the workspace, so account
groups that aren't (often the ones Unity Catalog grants name) are read from the
account with the user's own token and added.
"""
from types import SimpleNamespace

import pytest

from app.api import deps
from app.core.config import settings

EMAIL = "member@example.com"


@pytest.fixture
def fake_client(monkeypatch):
    """``deps.WorkspaceClient`` stand-in: workspace ``me()`` groups plus account ``/Me`` groups."""
    state = {"workspace": ["users", "team.data"], "account": ["team.data", "edh_dbx_metastore_admin"],
             "account_error": None, "account_calls": 0, "clients": []}

    class _ApiClient:
        def do(self, method, path, query=None, **kw):
            assert (method, path) == ("GET", deps.ACCOUNT_GROUPS_ME_PATH)
            state["account_calls"] += 1
            if state["account_error"]:
                raise state["account_error"]
            return {"groups": [{"display": g, "type": "direct"} for g in state["account"]]}

    class _Client:
        def __init__(self, *a, **kw):
            state["clients"].append(kw)
            self.api_client = _ApiClient()
            self.current_user = SimpleNamespace(me=lambda: SimpleNamespace(
                groups=[SimpleNamespace(display=g) for g in state["workspace"]], roles=[]))

    monkeypatch.setattr(deps, "WorkspaceClient", _Client)
    monkeypatch.setattr(settings, "DATABRICKS_HOST", "https://example.cloud.databricks.com")
    deps._account_group_cache.clear()
    yield state
    deps._account_group_cache.clear()


def test_account_groups_are_added_once_and_cached(fake_client):
    entitlements = deps._get_user_entitlements(EMAIL, "user-token")

    assert entitlements == [EMAIL, "users", "team.data", "edh_dbx_metastore_admin"]
    assert fake_client["clients"][0]["token"] == "user-token"
    deps._get_user_entitlements(EMAIL, "user-token")
    assert fake_client["account_calls"] == 1


def test_a_refused_account_read_keeps_workspace_groups_and_is_cached(fake_client):
    fake_client["account_error"] = RuntimeError("scope not granted")

    assert deps._get_user_entitlements(EMAIL, "user-token") == [EMAIL, "users", "team.data"]
    deps._get_user_entitlements(EMAIL, "user-token")
    assert fake_client["account_calls"] == 1


def test_the_cache_expires(fake_client, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(deps.time, "monotonic", lambda: clock["now"])

    deps._get_user_entitlements(EMAIL, "user-token")
    clock["now"] += deps.ACCOUNT_GROUP_CACHE_SECONDS + 1
    deps._get_user_entitlements(EMAIL, "user-token")
    assert fake_client["account_calls"] == 2


def test_account_groups_are_not_read_without_the_users_own_token(fake_client):
    """Without a user token the client is the app's identity, whose groups aren't the user's."""
    deps._get_user_entitlements(EMAIL, None)
    assert fake_client["account_calls"] == 0
