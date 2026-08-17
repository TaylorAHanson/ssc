"""Unit tests for the streaming AgentRunner.

These tests fake out the LLM client and any tools so we can focus on
the runner's event-emission protocol: status / tool_call / tool_result
/ pending_poll / message / done.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from app.agents.events import (
    DoneEvent,
    MessageEvent,
    PendingPollEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.agents.runner import AgentRunner


class _FakeLLMResponse:
    """Convenience holder so tests can script multi-turn conversations."""

    def __init__(self, content: str = "", tool_calls: List[Dict[str, Any]] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []

    def to_dict(self) -> Dict[str, Any]:
        return {"content": self.content, "tool_calls": self.tool_calls}


class _FakeLLMClient:
    """Drives `AgentRunner.run_stream` through a scripted sequence."""

    def __init__(self, scripted: List[_FakeLLMResponse]):
        self._scripted = list(scripted)
        self.calls: List[Dict[str, Any]] = []

    async def generate_response(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            return {"content": "", "tool_calls": []}
        return self._scripted.pop(0).to_dict()


class _FakeTool:
    """Minimal stand-in for `McpTool` used by runner tests."""

    def __init__(self, name: str, result: Any, friendly_label: str = "Running..."):
        self.name = name
        self._result = result
        self._friendly_label = friendly_label
        self.input_schema = {"type": "object", "properties": {}, "required": []}
        self.description = "fake"
        self.required_role = None
        # Classification metadata the V2 ToolExecutor reads on every call.
        self.is_mutating = False
        self.side_effect_class = "read"
        self.policy_ref = None

    @property
    def friendly_label(self) -> str:
        return self._friendly_label

    @property
    def friendly_completion_label(self):
        return None

    async def execute(self, **kwargs):
        return self._result


def _make_runner(tools: List[Any], scripted: List[_FakeLLMResponse]) -> AgentRunner:
    """Build a runner with the LLM client patched to a scripted fake."""
    runner = AgentRunner(
        system_prompt="SYSTEM",
        tools=tools,
        user_identity={"email": "test@example.com"},
        max_iterations=3,
        mode="self_service",
    )
    runner.llm_client = _FakeLLMClient(scripted)  # type: ignore[assignment]
    return runner


@pytest.mark.asyncio
async def test_run_stream_terminal_message_path():
    """No tool calls => single iteration emits status, message, done."""
    runner = _make_runner(
        tools=[],
        scripted=[_FakeLLMResponse(content="Hello world")],
    )

    events = [ev async for ev in runner.run_stream(query="hi")]
    types = [type(ev) for ev in events]

    assert StatusEvent in types
    # Last meaningful event should be the message; the very last is `done`.
    assert any(isinstance(ev, MessageEvent) and ev.content == "Hello world" for ev in events)
    assert isinstance(events[-1], DoneEvent)


@pytest.mark.asyncio
async def test_run_stream_emits_tool_call_and_tool_result():
    """Tool call iteration => emits tool_call then tool_result."""
    fake_tool = _FakeTool(
        name="echo",
        result={"echoed": "ok"},
        friendly_label="Echoing...",
    )
    runner = _make_runner(
        tools=[fake_tool],
        scripted=[
            _FakeLLMResponse(
                tool_calls=[
                    {
                        "id": "tc-1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": {"q": "x"}},
                    }
                ]
            ),
            _FakeLLMResponse(content="Done."),
        ],
    )

    events = [ev async for ev in runner.run_stream(query="hi")]
    tool_calls = [ev for ev in events if isinstance(ev, ToolCallEvent)]
    tool_results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
    messages = [ev for ev in events if isinstance(ev, MessageEvent)]

    assert len(tool_calls) == 1
    assert tool_calls[0].name == "echo"
    assert tool_calls[0].friendly_label == "Echoing..."
    assert len(tool_results) == 1
    assert tool_results[0].ok is True
    assert any(m.content == "Done." for m in messages)


@pytest.mark.asyncio
async def test_run_stream_emits_pending_poll_and_halts():
    """Pending-poll envelope => emits pending_poll, no tool_result, done."""
    pending_envelope = {
        "pending_poll": {
            "kind": "genie",
            "friendly_label": "Asking Genie...",
            "space_id": "s",
            "conversation_id": "c",
            "message_id": "m",
            "question": "?",
        }
    }
    fake_tool = _FakeTool(
        name="ask_your_data",
        result=pending_envelope,
        friendly_label="Asking Genie...",
    )
    # Even though we provide a follow-up scripted response, the runner
    # should NOT call the LLM again after a pending_poll.
    fake_llm_followup_marker = _FakeLLMResponse(content="must not be reached")
    runner = _make_runner(
        tools=[fake_tool],
        scripted=[
            _FakeLLMResponse(
                tool_calls=[
                    {
                        "id": "tc-genie",
                        "type": "function",
                        "function": {"name": "ask_your_data", "arguments": {"question": "?"}},
                    }
                ]
            ),
            fake_llm_followup_marker,
        ],
    )

    events = [ev async for ev in runner.run_stream(query="ask")]
    types = [type(ev).__name__ for ev in events]

    pending = [ev for ev in events if isinstance(ev, PendingPollEvent)]
    tool_results = [ev for ev in events if isinstance(ev, ToolResultEvent)]

    assert len(pending) == 1
    assert pending[0].kind == "genie"
    assert pending[0].tool_call_id == "tc-genie"
    assert pending[0].ids["space_id"] == "s"
    # No tool_result for a pending poll - the poll lifecycle replaces it.
    assert tool_results == []
    # Stream still terminates with a `done` event so the UI closes the reader.
    assert types[-1] == "DoneEvent"
    # The follow-up scripted response should still be queued (LLM not re-called).
    assert fake_llm_followup_marker in runner.llm_client._scripted  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_truncated_tool_arguments_are_reported_not_silently_emptied():
    """A cut-off arguments blob must not reach the tool as {}.

    Real symptom: `save_workflow_draft` carries a whole graph_spec plus the
    runtime playbook, so its arguments outran the response-token ceiling and
    arrived as invalid JSON. Falling back to {} called the tool with nothing,
    which reported "Field required: key, graph_spec" — blaming the model for
    forgetting fields it had actually sent, and giving it no way to recover.
    """
    fake_tool = _FakeTool(name="save_workflow_draft", result={"ok": True})
    runner = _make_runner(
        tools=[fake_tool],
        scripted=[
            _FakeLLMResponse(
                tool_calls=[
                    {
                        "id": "tc-cut",
                        "type": "function",
                        "function": {
                            "name": "save_workflow_draft",
                            # Valid JSON right up to where it was severed.
                            "arguments": '{"key": "data_migration", "graph_spec": {"stages": [{"kind": "ga',
                        },
                    }
                ]
            ),
            _FakeLLMResponse(content="Retrying smaller."),
        ],
    )

    events = [ev async for ev in runner.run_stream(query="build it")]
    results = [ev for ev in events if isinstance(ev, ToolResultEvent)]

    assert len(results) == 1
    assert results[0].ok is False
    assert "not valid JSON" in (results[0].error or "")
    # The agent is told the size and what to do next, not just "it failed".
    assert "cut off" in (results[0].error or "")
    # And the tool itself was never invoked with empty arguments.
    assert not any(isinstance(ev, ToolCallEvent) for ev in events)


@pytest.mark.asyncio
async def test_the_agent_loop_asks_for_enough_output_tokens_for_a_payload_call():
    """The client's 2000-token default truncates a workflow save mid-JSON."""
    from app.core.config import settings

    runner = _make_runner(tools=[], scripted=[_FakeLLMResponse(content="hi")])
    [ev async for ev in runner.run_stream(query="hi")]

    call = runner.llm_client.calls[0]  # type: ignore[attr-defined]
    assert call["max_tokens"] == settings.AGENT_MAX_RESPONSE_TOKENS
    assert settings.AGENT_MAX_RESPONSE_TOKENS >= 8000


@pytest.mark.asyncio
async def test_run_shim_drains_stream_into_legacy_dict():
    """`runner.run` returns the dict shape used by background callers."""
    runner = _make_runner(
        tools=[],
        scripted=[_FakeLLMResponse(content="Hello")],
    )
    result = await runner.run(query="hi")
    assert result["content"] == "Hello"
    assert result["pending_poll"] is None
    # Messages list should include system + user (and any assistant turn
    # if tools were called - none here, so just system + user).
    assert any(m.get("role") == "system" for m in result["messages"])
    assert any(m.get("role") == "user" and m.get("content") == "hi" for m in result["messages"])
