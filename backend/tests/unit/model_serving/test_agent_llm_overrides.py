"""Per-caller model / reasoning-effort overrides on AgentLLMClient.

The app code reviewer uses a different model than the chat agent (the agent's
fast tool model often runs with reasoning off). An override must not leak the
agent's reasoning effort, and the default must keep following the agent's
live setting.
"""
import pytest

import app.model_serving.agent_llm as agent_llm
from app.core.config import settings


@pytest.fixture
def client_factory(monkeypatch):
    monkeypatch.setattr(agent_llm, "get_model_serving_client", lambda: object())
    monkeypatch.setattr(settings, "AI_GATEWAY_ENDPOINT", "")
    monkeypatch.setattr(settings, "MODEL_SERVING_AGENT_LLM_ENDPOINT", "databricks-gpt-6-luna")
    monkeypatch.setattr(settings, "AGENT_LLM_REASONING_EFFORT", "none")
    return agent_llm.AgentLLMClient


def _routed(client):
    inputs = {"messages": [], "temperature": 0.0}
    client._apply_routing(inputs)
    return inputs


def test_default_follows_the_agents_model_and_effort(client_factory):
    client = client_factory()
    assert client.endpoint_name == "databricks-gpt-6-luna"
    assert _routed(client)["reasoning_effort"] == "none"


def test_override_uses_its_own_model_and_blank_effort_omits_it(client_factory):
    client = client_factory(model="databricks-claude-sonnet-5", reasoning_effort="")
    assert client.endpoint_name == "databricks-claude-sonnet-5"
    assert "reasoning_effort" not in _routed(client)


def test_override_effort_is_sent_as_given(client_factory):
    assert _routed(client_factory(model="m", reasoning_effort="high"))["reasoning_effort"] == "high"
