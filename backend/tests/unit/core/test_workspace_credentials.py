"""Credential resolution for target workspaces.

Model: one secret scope per installation
(``settings.TARGET_WORKSPACE_SP_SECRET_SCOPE``) plus target workspaces that name
their SP secret keys inline. Validated without hitting Databricks by
monkeypatching the secret reader to a {(scope, key): value} map.
"""
import pytest

from app.core import workspaces as ws


@pytest.fixture(autouse=True)
def _clear_secret_cache():
    ws._secret_cache.clear()
    yield
    ws._secret_cache.clear()


def _fake_secrets(monkeypatch, mapping):
    """Patch _read_secret to resolve from a {(scope, key): value} mapping."""
    monkeypatch.setattr(ws, "_read_secret", lambda scope, key: mapping.get((scope, key)))


def _config(monkeypatch, cfg):
    monkeypatch.setattr(ws, "_yaml_config", cfg)


def _scope(monkeypatch, scope):
    monkeypatch.setattr(ws.settings, "TARGET_WORKSPACE_SP_SECRET_SCOPE", scope, raising=False)


def test_workspace_uses_inline_sp_keys(monkeypatch):
    """A workspace resolves its SP from the install scope + inline key names."""
    _scope(monkeypatch, "install_scope")
    _config(
        monkeypatch,
        {
            "target_workspaces": [
                {
                    "name": "prod-a",
                    "host": "https://a",
                    "environment": "prod",
                    "client_id_key": "sp_prod_client_id",
                    "client_secret_key": "sp_prod_client_secret",
                }
            ],
        },
    )
    _fake_secrets(
        monkeypatch,
        {
            ("install_scope", "sp_prod_client_id"): "prod-cid",
            ("install_scope", "sp_prod_client_secret"): "prod-secret",
        },
    )

    w = ws.get_target_workspaces()[0]
    assert w.client_id == "prod-cid"
    assert w.client_secret == "prod-secret"
    assert w.credential_source == "workspace_sp:install_scope"


def test_workspaces_can_use_different_or_shared_sps(monkeypatch):
    """Each workspace names its own SP; two can share by using the same keys."""
    _scope(monkeypatch, "install_scope")
    _config(
        monkeypatch,
        {
            "target_workspaces": [
                {"name": "dev-a", "host": "https://a", "environment": "dev",
                 "client_id_key": "sp_dev_client_id", "client_secret_key": "sp_dev_client_secret"},
                {"name": "dev-b", "host": "https://b", "environment": "dev",
                 "client_id_key": "sp_dev_client_id", "client_secret_key": "sp_dev_client_secret"},
                {"name": "prod-a", "host": "https://p", "environment": "prod",
                 "client_id_key": "sp_prod_client_id", "client_secret_key": "sp_prod_client_secret"},
            ],
        },
    )
    _fake_secrets(
        monkeypatch,
        {
            ("install_scope", "sp_dev_client_id"): "dev-cid",
            ("install_scope", "sp_dev_client_secret"): "dev-secret",
            ("install_scope", "sp_prod_client_id"): "prod-cid",
            ("install_scope", "sp_prod_client_secret"): "prod-secret",
        },
    )

    result = {w.name: w for w in ws.get_target_workspaces()}
    # dev-a and dev-b share the same SP.
    assert result["dev-a"].client_id == "dev-cid"
    assert result["dev-b"].client_id == "dev-cid"
    # prod-a uses a different SP.
    assert result["prod-a"].client_id == "prod-cid"


def test_scope_comes_from_settings(monkeypatch):
    """The scope is the install-wide setting (databricks.yml / admin Settings)."""
    _scope(monkeypatch, "scope_from_settings")
    _config(
        monkeypatch,
        {
            "target_workspaces": [
                {"name": "dev-a", "host": "https://a", "environment": "dev",
                 "client_id_key": "sp_dev_client_id", "client_secret_key": "sp_dev_client_secret"}
            ],
        },
    )
    _fake_secrets(
        monkeypatch,
        {
            ("scope_from_settings", "sp_dev_client_id"): "cid",
            ("scope_from_settings", "sp_dev_client_secret"): "secret",
            # A different scope intentionally has no values.
            ("other_scope", "sp_dev_client_id"): "wrong",
        },
    )

    w = ws.get_target_workspaces()[0]
    assert w.client_id == "cid"
    assert w.credential_source == "workspace_sp:scope_from_settings"


def test_blank_sp_keys_fall_back_to_global_default(monkeypatch):
    """A workspace with no inline SP keys uses the app's own SP / PAT."""
    _scope(monkeypatch, "install_scope")
    _config(
        monkeypatch,
        {"target_workspaces": [{"name": "x", "host": "https://x", "environment": "dev"}]},
    )
    _fake_secrets(monkeypatch, {})
    monkeypatch.setattr(ws.settings, "DATABRICKS_CLIENT_ID", "global-cid", raising=False)
    monkeypatch.setattr(ws.settings, "DATABRICKS_CLIENT_SECRET", "global-secret", raising=False)
    monkeypatch.setattr(ws.settings, "DATABRICKS_TOKEN", None, raising=False)

    w = ws.get_target_workspaces()[0]
    assert w.client_id == "global-cid"
    assert w.credential_source == "global_default"


def test_missing_scope_falls_back_to_global_default(monkeypatch):
    """Inline SP keys but no configured scope => fall back to the app's own SP."""
    _scope(monkeypatch, "")
    _config(
        monkeypatch,
        {
            "target_workspaces": [
                {"name": "dev-a", "host": "https://a", "environment": "dev",
                 "client_id_key": "sp_dev_client_id", "client_secret_key": "sp_dev_client_secret"}
            ],
        },
    )
    _fake_secrets(monkeypatch, {})
    monkeypatch.setattr(ws.settings, "DATABRICKS_CLIENT_ID", "global-cid", raising=False)
    monkeypatch.setattr(ws.settings, "DATABRICKS_CLIENT_SECRET", "global-secret", raising=False)
    monkeypatch.setattr(ws.settings, "DATABRICKS_TOKEN", None, raising=False)

    w = ws.get_target_workspaces()[0]
    assert w.client_id == "global-cid"
    assert w.credential_source == "global_default"
