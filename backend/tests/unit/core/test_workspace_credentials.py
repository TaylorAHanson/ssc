"""Credential-resolution precedence for target workspaces.

Validates the per-environment shared SP + per-workspace override ("hybrid")
behavior in app.core.workspaces without hitting Databricks: the secret reader
is monkeypatched to a fake scope/key -> value map.
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


def test_workspace_inherits_environment_sp(monkeypatch):
    """A workspace with no own creds uses its environment's shared SP."""
    _config(
        monkeypatch,
        {
            "service_principals": {
                "secret_scope": "scope_prd",
                "environments": {
                    "dev": {
                        "client_id_key": "sp_dbxgrc_dev_client_id",
                        "client_secret_key": "sp_dbxgrc_dev_client_secret",
                    }
                },
            },
            "target_workspaces": [
                {"name": "dev-a", "host": "https://a", "environment": "dev"},
                {"name": "dev-b", "host": "https://b", "environment": "dev"},
            ],
        },
    )
    _fake_secrets(
        monkeypatch,
        {
            ("scope_prd", "sp_dbxgrc_dev_client_id"): "dev-cid",
            ("scope_prd", "sp_dbxgrc_dev_client_secret"): "dev-secret",
        },
    )

    result = {w.name: w for w in ws.get_target_workspaces()}

    assert result["dev-a"].client_id == "dev-cid"
    assert result["dev-a"].client_secret == "dev-secret"
    assert result["dev-a"].credential_source == "environment_sp:dev"
    # Both dev workspaces share the same SP.
    assert result["dev-b"].client_id == "dev-cid"


def test_per_workspace_secret_override_wins(monkeypatch):
    """A single dev workspace can pin a different SP than the rest (hybrid)."""
    _config(
        monkeypatch,
        {
            "service_principals": {
                "secret_scope": "scope_prd",
                "environments": {
                    "dev": {
                        "client_id_key": "sp_dbxgrc_dev_client_id",
                        "client_secret_key": "sp_dbxgrc_dev_client_secret",
                    }
                },
            },
            "target_workspaces": [
                {"name": "dev-a", "host": "https://a", "environment": "dev"},
                {
                    "name": "dev-special",
                    "host": "https://s",
                    "environment": "dev",
                    "client_id_secret": "sp_special_client_id",
                    "client_secret_secret": "sp_special_client_secret",
                },
            ],
        },
    )
    _fake_secrets(
        monkeypatch,
        {
            ("scope_prd", "sp_dbxgrc_dev_client_id"): "dev-cid",
            ("scope_prd", "sp_dbxgrc_dev_client_secret"): "dev-secret",
            ("scope_prd", "sp_special_client_id"): "special-cid",
            ("scope_prd", "sp_special_client_secret"): "special-secret",
        },
    )

    result = {w.name: w for w in ws.get_target_workspaces()}

    assert result["dev-a"].client_id == "dev-cid"
    assert result["dev-special"].client_id == "special-cid"
    assert result["dev-special"].credential_source == "workspace_secret:scope_prd"


def test_per_workspace_secret_scope_override(monkeypatch):
    """A workspace can also point at a completely different secret scope."""
    _config(
        monkeypatch,
        {
            "service_principals": {
                "secret_scope": "scope_prd",
                "environments": {
                    "dev": {"client_id_key": "sp_dbxgrc_dev_client_id"}
                },
            },
            "target_workspaces": [
                {
                    "name": "dev-team",
                    "host": "https://t",
                    "environment": "dev",
                    "secret_scope": "team_scope",
                    "client_id_secret": "team_cid",
                    "client_secret_secret": "team_secret",
                }
            ],
        },
    )
    _fake_secrets(
        monkeypatch,
        {
            ("team_scope", "team_cid"): "team-cid",
            ("team_scope", "team_secret"): "team-secret",
        },
    )

    w = ws.get_target_workspaces()[0]
    assert w.client_id == "team-cid"
    assert w.credential_source == "workspace_secret:team_scope"


def test_env_var_override_takes_top_precedence(monkeypatch):
    """Per-workspace env vars beat both secret tiers (back-compat path)."""
    monkeypatch.setenv("DEV_WS_CLIENT_ID", "env-cid")
    monkeypatch.setenv("DEV_WS_CLIENT_SECRET", "env-secret")
    _config(
        monkeypatch,
        {
            "service_principals": {
                "secret_scope": "scope_prd",
                "environments": {
                    "dev": {"client_id_key": "sp_dbxgrc_dev_client_id"}
                },
            },
            "target_workspaces": [
                {
                    "name": "dev-a",
                    "host": "https://a",
                    "environment": "dev",
                    "client_id_env": "DEV_WS_CLIENT_ID",
                    "client_secret_env": "DEV_WS_CLIENT_SECRET",
                }
            ],
        },
    )
    _fake_secrets(monkeypatch, {("scope_prd", "sp_dbxgrc_dev_client_id"): "dev-cid"})

    w = ws.get_target_workspaces()[0]
    assert w.client_id == "env-cid"
    assert w.credential_source == "workspace_env"


def test_databricks_yml_scope_overrides_config_default(monkeypatch):
    """TARGET_WORKSPACE_SP_SECRET_SCOPE (databricks.yml) beats the yaml default."""
    monkeypatch.setattr(ws.settings, "TARGET_WORKSPACE_SP_SECRET_SCOPE", "scope_from_dab", raising=False)
    _config(
        monkeypatch,
        {
            "service_principals": {
                "secret_scope": "yaml_default_scope",
                "environments": {
                    "dev": {
                        "client_id_key": "sp_dbxgrc_dev_client_id",
                        "client_secret_key": "sp_dbxgrc_dev_client_secret",
                    }
                },
            },
            "target_workspaces": [
                {"name": "dev-a", "host": "https://a", "environment": "dev"}
            ],
        },
    )
    _fake_secrets(
        monkeypatch,
        {
            ("scope_from_dab", "sp_dbxgrc_dev_client_id"): "dab-cid",
            ("scope_from_dab", "sp_dbxgrc_dev_client_secret"): "dab-secret",
            # The yaml-default scope intentionally has no values; if precedence
            # were wrong, resolution would miss and fall to the global default.
        },
    )

    w = ws.get_target_workspaces()[0]
    assert w.client_id == "dab-cid"
    assert w.credential_source == "environment_sp:dev"


def test_falls_back_to_global_default(monkeypatch):
    """No env/secret/per-env config -> the app's own SP (back-compat)."""
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
