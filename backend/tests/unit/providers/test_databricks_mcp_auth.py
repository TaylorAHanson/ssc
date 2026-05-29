"""Unit tests for ``app.providers.databricks_mcp.client`` auth resolution.

These tests lock in three behaviors the deployed Databricks App relies
on:

1. The OBO token, when present, is always preferred and is returned
   verbatim with ``source == "obo"``.
2. The service-principal fallback is **only** taken in true local
   runs — ``./dev.sh`` outside Databricks Apps with a local-flavored
   ``ENVIRONMENT``. Inside the Databricks Apps runtime the resolver
   returns ``(None, "none")`` so the caller can surface a clear
   "no auth" error rather than silently calling Genie under the
   service principal (which would almost certainly hit a 403, since
   the SP typically has weaker Genie permissions than the user).
3. Any non-local ``ENVIRONMENT`` value also blocks the fallback even
   when the Databricks Apps env vars aren't present (defensive
   default for unusual deployment shapes).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.providers.databricks_mcp import client as mcp_client


@pytest.fixture(autouse=True)
def _clear_databricks_apps_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: assume we are *not* running inside Databricks Apps.

    Tests that need the platform-runtime branch will set the env vars
    themselves. This guarantees a clean baseline regardless of the
    developer's shell.
    """
    monkeypatch.delenv("DATABRICKS_APP_PORT", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)


def _stub_settings(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setattr(mcp_client.settings, "ENVIRONMENT", environment, raising=False)
    monkeypatch.setattr(mcp_client.settings, "DATABRICKS_CLIENT_ID", "sp-id", raising=False)
    monkeypatch.setattr(mcp_client.settings, "DATABRICKS_CLIENT_SECRET", "sp-secret", raising=False)
    monkeypatch.setattr(
        mcp_client.settings,
        "DATABRICKS_HOST",
        "https://example.cloud.databricks.com",
        raising=False,
    )


def test_obo_token_is_always_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_settings(monkeypatch, environment="production")

    token, source = mcp_client.resolve_genie_bearer_token("user-obo-token")

    assert token == "user-obo-token"
    assert source == "obo"


def test_obo_preferred_even_inside_databricks_apps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OBO presence wins regardless of runtime detection."""
    _stub_settings(monkeypatch, environment="dev")
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")

    token, source = mcp_client.resolve_genie_bearer_token("user-obo-token")

    assert token == "user-obo-token"
    assert source == "obo"


def test_databricks_apps_runtime_blocks_sp_fallback_even_when_env_is_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The platform-runtime check must override a permissive ENVIRONMENT.

    Bundle target ``dev`` sets ``ENVIRONMENT=dev`` but is still
    deployed inside Databricks Apps — and therefore must refuse the SP
    fallback.
    """
    _stub_settings(monkeypatch, environment="dev")
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")

    with patch("databricks.sdk.core.Config") as fake_config:
        token, source = mcp_client.resolve_genie_bearer_token(None)
        fake_config.assert_not_called()

    assert (token, source) == (None, "none")


def test_databricks_apps_runtime_via_app_name_also_blocks_sp_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive secondary signal: ``DATABRICKS_APP_NAME`` alone is enough."""
    _stub_settings(monkeypatch, environment="local")
    monkeypatch.delenv("DATABRICKS_APP_PORT", raising=False)
    monkeypatch.setenv("DATABRICKS_APP_NAME", "edh-ssc-dev")

    with patch("databricks.sdk.core.Config") as fake_config:
        token, source = mcp_client.resolve_genie_bearer_token(None)
        fake_config.assert_not_called()

    assert (token, source) == (None, "none")


def test_production_env_off_platform_still_refuses_sp_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: a non-local ENVIRONMENT blocks fallback even off-platform."""
    _stub_settings(monkeypatch, environment="production")

    with patch("databricks.sdk.core.Config") as fake_config:
        token, source = mcp_client.resolve_genie_bearer_token(None)
        fake_config.assert_not_called()

    assert (token, source) == (None, "none")


@pytest.mark.parametrize(
    "env", ["development", "dev", "local", "test", "testing", "DEV"]
)
def test_local_environments_off_platform_allow_sp_fallback(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    _stub_settings(monkeypatch, environment=env)

    class _FakeCfg:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def authenticate(self) -> dict[str, str]:
            return {"Authorization": "Bearer sp-token-123"}

    with patch("databricks.sdk.core.Config", _FakeCfg):
        token, source = mcp_client.resolve_genie_bearer_token(None)

    assert token == "sp-token-123"
    assert source == "sp"


def test_default_environment_is_treated_as_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ENVIRONMENT`` defaults to ``production`` in :mod:`app.core.config`.

    A deployed app that simply forgot to set the variable should still
    be safe — i.e. it should *not* fall back to the SP.
    """
    _stub_settings(monkeypatch, environment="production")

    token, source = mcp_client.resolve_genie_bearer_token(None)

    assert (token, source) == (None, "none")
