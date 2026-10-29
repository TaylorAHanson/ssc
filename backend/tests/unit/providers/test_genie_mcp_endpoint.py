"""Which Genie MCP endpoint a call goes to, and what a refused call tells the user."""
from __future__ import annotations

import httpx
import pytest

from app.providers.databricks_mcp import client as mcp_client
from app.providers.databricks_mcp import build_genie_mcp_url, describe_genie_failure

HOST = "https://example.cloud.databricks.com"


@pytest.fixture(autouse=True)
def _no_workspace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABRICKS_WORKSPACE_ID", raising=False)


def test_workspace_genie_goes_through_unity_gateway():
    assert build_genie_mcp_url(host=HOST) == f"{HOST}/ai-gateway/mcp-services/system.ai.genie_one_mcp"


def test_the_workspace_id_from_the_apps_runtime_is_passed_as_o(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABRICKS_WORKSPACE_ID", "3983829797699651")
    assert build_genie_mcp_url(host=f"{HOST}/") == (
        f"{HOST}/ai-gateway/mcp-services/system.ai.genie_one_mcp?o=3983829797699651"
    )


def test_a_genie_space_keeps_its_own_endpoint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABRICKS_WORKSPACE_ID", "3983829797699651")
    assert build_genie_mcp_url(space_id="space1", host=HOST) == f"{HOST}/api/2.0/mcp/genie/space1"


def test_no_host_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_client.settings, "DATABRICKS_HOST", "", raising=False)
    monkeypatch.setattr(mcp_client.settings, "DATABRICKS_WORKSPACE_URL", "", raising=False)
    with pytest.raises(ValueError):
        build_genie_mcp_url()


def _refused(status: int) -> BaseException:
    request = httpx.Request("POST", f"{HOST}/ai-gateway/mcp-services/system.ai.genie_one_mcp")
    error = httpx.HTTPStatusError("refused", request=request, response=httpx.Response(status, request=request))
    return BaseExceptionGroup("unhandled errors in a TaskGroup", [error])


def test_a_refused_workspace_call_names_the_grant_to_ask_for():
    message = describe_genie_failure(_refused(403))
    assert "HTTP 403" in message
    assert "EXECUTE on system.ai.genie_one_mcp" in message


def test_a_refused_space_call_points_at_the_space():
    message = describe_genie_failure(_refused(401), space_id="space1")
    assert "Genie space space1" in message
    assert "genie_one_mcp" not in message


def test_other_failures_keep_the_original_error():
    assert describe_genie_failure(_refused(500), prefix="Poll failed").startswith("Poll failed: ")
    assert describe_genie_failure(RuntimeError("boom")) == "Failed to contact Genie: boom"
