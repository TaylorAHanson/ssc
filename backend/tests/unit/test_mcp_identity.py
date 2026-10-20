"""The MCP endpoint resolves the caller's full identity and never acts as the app (ADR-0006 D7)."""
import asyncio

import pytest

from app import mcp_server
from app.core.config import settings
from app.tools.mcp import tool


@tool(name="probe_read")
def _read_tool(**kwargs):
    return {"ok": True, "email": kwargs.get("_user_email"), "actor_type": kwargs.get("_actor_type")}


@tool(name="probe_whoami", requires_identity=True)
def _identity_tool(**kwargs):
    return {"ok": True, "email": kwargs.get("_user_email"), "roles": kwargs.get("_user_roles")}


@tool(name="probe_write", side_effect_class="app_write", allow_service_principals=True)
def _write_tool(**kwargs):
    return {"ok": True, "actor_type": kwargs.get("_actor_type"), "principal": kwargs.get("_principal_id")}


def _run(t, headers, monkeypatch):
    monkeypatch.setattr(mcp_server, "_request_headers", lambda: headers)
    return asyncio.run(mcp_server._governed_shim(t)())


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")


@pytest.fixture
def resolved(monkeypatch):
    seen = {}

    def fake(fwd, obo):
        seen["fwd"], seen["obo"] = fwd, obo
        if fwd.get("email"):
            return {"email": fwd["email"], "roles": "Governance Admin", "entitlements": "data-team",
                    "actor_type": "user", "application_id": None}
        return {"email": "app-1", "roles": "", "entitlements": "app-1",
                "actor_type": "service_principal", "application_id": "app-1"}

    monkeypatch.setattr(mcp_server, "_resolve_identity", fake)
    return seen


def test_identity_dependent_and_write_tools_refuse_without_a_forwarded_identity(production, resolved,
                                                                                monkeypatch):
    for t in (_identity_tool, _write_tool):
        result = _run(t, {}, monkeypatch)
        assert result["status"] == "refused"
    assert "fwd" not in resolved


def test_a_plain_read_still_runs_without_identity(production, resolved, monkeypatch):
    result = _run(_read_tool, {}, monkeypatch)

    assert result["ok"] is True
    assert result["email"] is None


def test_tools_receive_the_callers_roles_and_groups(production, resolved, monkeypatch):
    result = _run(_identity_tool, {"x-forwarded-email": "jane@corp.com", "x-forwarded-access-token": "tok"},
                  monkeypatch)

    assert result == {"ok": True, "email": "jane@corp.com", "roles": "Governance Admin"}
    assert resolved["obo"] == "tok"


def test_a_service_principal_reaches_only_tools_that_accept_one(production, resolved, monkeypatch):
    headers = {"x-forwarded-user": "app-1"}

    assert _run(_identity_tool, headers, monkeypatch)["status"] == "refused"
    result = _run(_write_tool, headers, monkeypatch)
    assert result == {"ok": True, "actor_type": "service_principal", "principal": "app-1"}


def test_a_caller_cannot_claim_to_be_a_principal(production, resolved, monkeypatch):
    headers = {"x-forwarded-email": "jane@corp.com"}
    monkeypatch.setattr(mcp_server, "_request_headers", lambda: headers)

    result = asyncio.run(mcp_server._governed_shim(_write_tool)(_actor_type="service_principal",
                                                                 _principal_id="app-1"))

    assert result["actor_type"] == "user"
    assert result["principal"] is None
