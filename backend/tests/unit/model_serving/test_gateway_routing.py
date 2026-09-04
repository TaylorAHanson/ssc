"""Every LLM caller must reach the model the admin actually configured.

When `AI_GATEWAY_ENDPOINT` is set, its value is a MODEL name (e.g.
``system.ai.gpt-5-6-luna``) that belongs in the request body, posted to the
gateway's chat/completions route. It is NOT a serving-endpoint name, so
``/serving-endpoints/{it}/invocations`` 404s.

Four call sites resolved the endpoint themselves and skipped that adaptation, so
the chat agent worked while the workflow-test judge, the instructions generator,
the test-case generator, and the trace judge all failed with "Endpoint
'system.ai.gpt-5-6-luna' not found". These tests pin the routing in one place and
assert the callers go through it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.model_serving.agent_llm import AgentLLMClient


class _RecordingTransport:
    """Stands in for ModelServingClient, capturing how it was invoked."""

    def __init__(self, response: Optional[Dict[str, Any]] = None):
        self.calls: List[Dict[str, Any]] = []
        self._response = response or {
            "choices": [{"message": {"role": "assistant", "content": "{}"}}]
        }

    async def invoke_endpoint(self, endpoint_name, inputs, endpoint_url=None,
                             use_foundation_model_format=False):
        self.calls.append(
            {
                "endpoint_name": endpoint_name,
                "inputs": inputs,
                "endpoint_url": endpoint_url,
            }
        )
        return self._response


def _client(monkeypatch, *, gateway: str = "", direct: str = "",
            effort: str = "") -> tuple[AgentLLMClient, _RecordingTransport]:
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_GATEWAY_ENDPOINT", gateway, raising=False)
    monkeypatch.setattr(settings, "MODEL_SERVING_AGENT_LLM_ENDPOINT", direct, raising=False)
    monkeypatch.setattr(settings, "AGENT_LLM_REASONING_EFFORT", effort, raising=False)
    transport = _RecordingTransport()
    client = AgentLLMClient()
    client.client = transport  # type: ignore[assignment]
    return client, transport


@pytest.mark.asyncio
async def test_a_gateway_model_is_named_in_the_body_not_the_path(monkeypatch):
    client, transport = _client(monkeypatch, gateway="system.ai.gpt-5-6-luna")
    await client.complete_text("grade this")

    call = transport.calls[0]
    assert call["endpoint_url"] == "/ai-gateway/mlflow/v1/chat/completions"
    assert call["inputs"]["model"] == "system.ai.gpt-5-6-luna"


@pytest.mark.asyncio
async def test_a_direct_endpoint_keeps_the_serving_path(monkeypatch):
    client, transport = _client(monkeypatch, direct="databricks-gpt-5-4-mini")
    await client.complete_text("grade this")

    call = transport.calls[0]
    assert call["endpoint_url"] is None  # transport builds /serving-endpoints/...
    assert "model" not in call["inputs"]
    assert call["endpoint_name"] == "databricks-gpt-5-4-mini"


@pytest.mark.asyncio
async def test_reasoning_effort_is_applied_to_one_shot_calls_too(monkeypatch):
    """A model that requires reasoning_effort="none" requires it everywhere."""
    client, transport = _client(
        monkeypatch, gateway="system.ai.gpt-5-6-luna", effort="none"
    )
    await client.complete_text("grade this")
    assert transport.calls[0]["inputs"]["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_blank_effort_is_omitted_so_non_reasoning_models_do_not_400(monkeypatch):
    client, transport = _client(monkeypatch, direct="databricks-claude-sonnet")
    await client.complete_text("grade this")
    assert "reasoning_effort" not in transport.calls[0]["inputs"]


@pytest.mark.asyncio
async def test_complete_text_raises_instead_of_returning_a_friendly_sentence(monkeypatch):
    """`generate_response` answers transport failures with prose, which a judge
    would happily score. One-shot callers need the exception."""
    client, transport = _client(monkeypatch, gateway="system.ai.gpt-5-6-luna")

    async def boom(*a, **k):
        raise RuntimeError("Endpoint not found")

    transport.invoke_endpoint = boom  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="Endpoint not found"):
        await client.complete_text("grade this")


@pytest.mark.asyncio
async def test_gemini_models_omit_temperature(monkeypatch):
    """Gemini models (e.g. gemini-3.8-flash) reject the temperature parameter on Databricks Model Serving."""
    client, transport = _client(monkeypatch, gateway="gemini-3.8-flash")
    await client.complete_text("test prompt", temperature=0.7)

    call = transport.calls[0]
    assert "temperature" not in call["inputs"]


@pytest.mark.asyncio
async def test_non_gemini_models_preserve_temperature(monkeypatch):
    """Standard models preserve the configured temperature parameter."""
    client, transport = _client(monkeypatch, direct="databricks-claude-sonnet")
    await client.complete_text("test prompt", temperature=0.7)

    call = transport.calls[0]
    assert call["inputs"]["temperature"] == 0.7


@pytest.mark.asyncio
async def test_model_serving_client_retries_on_unsupported_temperature():
    """If an endpoint returns 400 rejecting temperature, ModelServingClient retries once without temperature."""
    import httpx
    from app.model_serving.client import ModelServingClient

    attempt = 0

    class MockAsyncClient:
        async def post(self, url, json=None):
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                req = httpx.Request("POST", "http://test" + url)
                resp = httpx.Response(
                    400,
                    request=req,
                    json={
                        "error_code": "BAD_REQUEST",
                        "message": "BAD_REQUEST: Model gemini-3.8-flash does not support the temperature parameter.",
                    },
                )
                raise httpx.HTTPStatusError("Bad Request", request=req, response=resp)
            # Second attempt (retry without temperature)
            req = httpx.Request("POST", "http://test" + url)
            assert "temperature" not in json
            return httpx.Response(
                200,
                request=req,
                json={"choices": [{"message": {"role": "assistant", "content": "Hello!"}}]},
            )

    client = ModelServingClient()
    client.base_url = "http://test"
    client.client = MockAsyncClient()  # type: ignore[assignment]
    # Mock token freshness
    client._token_is_stale = lambda: False  # type: ignore[assignment]

    inputs = {"messages": [{"role": "user", "content": "hi"}], "temperature": 0.5}
    res = await client.invoke_endpoint("custom-gemini-endpoint", inputs, use_foundation_model_format=True)
    assert attempt == 2
    assert res == {"message": {"role": "assistant", "content": "Hello!"}}


def test_no_llm_caller_resolves_the_endpoint_by_hand():
    """Guard the pattern, not just today's four bugs: resolving the endpoint name
    and POSTing it yourself silently breaks the moment a gateway model is set."""
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[3] / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        # agent_llm.py IS the one place allowed to know this.
        if path.name == "agent_llm.py":
            continue
        text = path.read_text()
        if "AI_GATEWAY_ENDPOINT" in text and "invoke_endpoint" in text:
            offenders.append(str(path.relative_to(app_dir)))
    assert offenders == [], (
        "These modules resolve the LLM endpoint themselves instead of using "
        f"AgentLLMClient, so they break on a gateway model: {offenders}"
    )
