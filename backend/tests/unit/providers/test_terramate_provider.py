"""
Unit tests for TerramateProvider.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from app.core.exceptions import PermanentError, RetryableError
from app.providers.terramate.client import TerramateProvider


@pytest.fixture
def provider():
    return TerramateProvider(
        api_url="http://mock-terramate:8000",
        api_token="test-token",
        timeout_seconds=5,
    )


@pytest.mark.asyncio
async def test_create_request_success(provider):
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {
        "request_id": "11111111-2222-3333-4444-555555555555",
        "status": "pending",
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        result = await provider.create_request(
            request_type="workspace",
            params={"name": "test-ws"},
            idempotency_key="key-123",
            requester="user@test.com",
        )

        assert result["success"] is True
        assert result["request_id"] == "11111111-2222-3333-4444-555555555555"
        assert result["status"] == "pending"

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"] == {"type": "workspace", "params": {"name": "test-ws"}}
        assert call_kwargs["headers"]["Idempotency-Key"] == "key-123"
        assert call_kwargs["headers"]["X-Requester"] == "user@test.com"
        assert call_kwargs["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_create_request_intake_gate_503(provider):
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.json.return_value = {"detail": "Intake closed for maintenance"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        with pytest.raises(PermanentError, match="Terramate intake gate closed"):
            await provider.create_request(
                request_type="workspace",
                params={"name": "test-ws"},
                idempotency_key="key-123",
                requester="user@test.com",
            )


@pytest.mark.asyncio
async def test_create_request_validation_error_422(provider):
    mock_resp = MagicMock()
    mock_resp.status_code = 422
    mock_resp.json.return_value = {"detail": [{"loc": ["params", "name"], "msg": "field required"}]}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        with pytest.raises(PermanentError, match="Terramate parameter validation failed"):
            await provider.create_request(
                request_type="workspace",
                params={},
                idempotency_key="key-123",
                requester="user@test.com",
            )


@pytest.mark.asyncio
async def test_get_request_success_and_404(provider):
    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {
        "id": "req-123",
        "type": "workspace",
        "status": "applied",
        "steps": [{"ordinal": 0, "key": "workspace", "status": "applied"}],
    }

    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_200
        detail = await provider.get_request("req-123")
        assert detail is not None
        assert detail["id"] == "req-123"
        assert detail["status"] == "applied"

        mock_get.return_value = mock_resp_404
        detail_404 = await provider.get_request("nonexistent")
        assert detail_404 is None


@pytest.mark.asyncio
async def test_get_step_plan_available_and_409(provider):
    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {
        "ordinal": 0,
        "key": "workspace",
        "status": "planned",
        "plan": "+ databricks_mws_workspaces.this will be created",
    }

    mock_resp_409 = MagicMock()
    mock_resp_409.status_code = 409
    mock_resp_409.json.return_value = {"detail": "Plan not available yet"}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_200
        res = await provider.get_step_plan("req-123", ordinal=0)
        assert res["available"] is True
        assert "+ databricks_mws_workspaces" in res["plan"]

        mock_get.return_value = mock_resp_409
        res_409 = await provider.get_step_plan("req-123", ordinal=0)
        assert res_409["available"] is False
        assert res_409["status"] == "pending_plan"


@pytest.mark.asyncio
async def test_cancel_request(provider):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "cancelled"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await provider.cancel_request("req-123")
        assert res["status"] == "cancelled"


@pytest.mark.asyncio
async def test_health_check(provider):
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        assert await provider.health_check() is True

        mock_get.side_effect = httpx.ConnectError("Connection refused")
        assert await provider.health_check() is False
