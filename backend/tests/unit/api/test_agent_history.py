"""Unit tests for ``_build_runner_and_history`` history translation.

These tests focus narrowly on how the API's ``ChatMessage`` wire shape
is converted into the chat-completion ``messages`` list the runner
hands to the model serving endpoint. The most important contract is
the ``user → assistant(tool_calls) → tool → ...`` linkage required by
the upstream API: a ``role: 'tool'`` message that isn't preceded by a
matching ``assistant.tool_calls`` causes a ``HTTP 400 BAD_REQUEST``
("messages with role 'tool' must be a response to a preceding
message with 'tool_calls'"). The UI synthesizes the assistant turn
on a continuation, and we must round-trip ``tool_calls`` faithfully.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.api.v1.agent import (
    ChatMessage,
    ConversationRequest,
    _build_runner_and_history,
)


def _fake_user(email: str = "user@example.com"):
    """Stand-in for the ``User`` dependency injected by FastAPI."""
    return SimpleNamespace(
        email=email,
        full_name="Test User",
        roles=[],
        entitlements=[],
        has_role=lambda _r: True,
    )


def _build(history: list[ChatMessage]):
    """Run the translation under settings that always enable the agent."""
    request = ConversationRequest(
        query="follow-up",
        conversation_history=history,
        context=None,
    )
    user = _fake_user()
    # ``_build_runner_and_history`` requires ``settings.AGENT_ENABLED``;
    # we trust the project default (``True``) to avoid monkeypatching
    # configuration at import time. ``db=None`` makes tool resolution fall back
    # to the static gating, and makes the user-context lookup degrade to no
    # block — this test only cares about history translation.
    _runner, wire_history, _mode = asyncio.run(
        _build_runner_and_history(request, user, None)
    )
    return wire_history


def test_assistant_message_with_tool_calls_round_trips_into_assistant_role() -> None:
    """An ``agent`` message carrying ``tool_calls`` must become a chat
    completion ``assistant`` entry with the same ``tool_calls`` block.

    Without this, the synthetic ``tool`` message that follows would be
    an orphan and the upstream API rejects the request.
    """
    history = [
        ChatMessage(
            id="u1",
            type="user",
            content="What tables have temporal columns?",
            timestamp="2026-05-29T17:00:00Z",
        ),
        ChatMessage(
            id="a1-tc",
            type="agent",
            content="",
            timestamp="2026-05-29T17:00:01Z",
            tool_calls=[
                {
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                        "name": "ask_your_data",
                        "arguments": '{"question":"Which tables have temporal columns?"}',
                    },
                }
            ],
        ),
        ChatMessage(
            id="t1",
            type="tool",
            content="adoc_dq_history.processed_at TIMESTAMP, ...",
            timestamp="2026-05-29T17:01:30Z",
            tool_call_id="call_abc123",
            name="ask_your_data",
        ),
    ]

    wire = _build(history)

    # User entry comes through unchanged.
    assert wire[0]["role"] == "user"
    assert wire[0]["content"] == "What tables have temporal columns?"

    # The agent turn must surface as an assistant message that still
    # carries its ``tool_calls`` block; the chat completion endpoint
    # uses this to validate the next ``role: 'tool'`` message.
    assert wire[1]["role"] == "assistant"
    assert wire[1]["content"] == ""
    assert "tool_calls" in wire[1]
    tool_calls = wire[1]["tool_calls"]
    assert isinstance(tool_calls, list) and len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_abc123"
    assert tool_calls[0]["function"]["name"] == "ask_your_data"

    # Tool result follows with the matching id.
    assert wire[2]["role"] == "tool"
    assert wire[2]["tool_call_id"] == "call_abc123"
    assert wire[2]["name"] == "ask_your_data"


def test_assistant_message_without_tool_calls_does_not_attach_an_empty_block() -> None:
    """A regular text-only assistant turn shouldn't sprout an empty
    ``tool_calls`` field — that would change the message shape and
    confuse downstream consumers."""
    history = [
        ChatMessage(
            id="u1",
            type="user",
            content="hi",
            timestamp="2026-05-29T17:00:00Z",
        ),
        ChatMessage(
            id="a1",
            type="agent",
            content="hello back",
            timestamp="2026-05-29T17:00:01Z",
        ),
    ]

    wire = _build(history)

    assert wire[1]["role"] == "assistant"
    assert wire[1]["content"] == "hello back"
    assert "tool_calls" not in wire[1]


def test_tool_message_preserves_call_linkage() -> None:
    """The ``tool`` message must keep its ``tool_call_id`` and ``name``
    even when the assistant turn is missing — falling back to ``id`` /
    ``"tool"`` matches the existing behavior the UI relied on before
    we added the synthetic assistant turn."""
    history = [
        ChatMessage(
            id="t1",
            type="tool",
            content="...",
            timestamp="2026-05-29T17:01:30Z",
            tool_call_id="call_abc123",
            name="ask_your_data",
        )
    ]

    wire = _build(history)

    assert wire[0]["role"] == "tool"
    assert wire[0]["tool_call_id"] == "call_abc123"
    assert wire[0]["name"] == "ask_your_data"


@pytest.mark.parametrize("missing_field", ["tool_call_id", "name"])
def test_tool_message_missing_fields_fall_back_to_safe_defaults(
    missing_field: str,
) -> None:
    """If the UI somehow omits ``tool_call_id`` or ``name``, fall back
    to the message ``id`` / ``"tool"`` rather than dropping the
    message — losing it would break the conversation entirely."""
    fields = {
        "id": "t1",
        "type": "tool",
        "content": "...",
        "timestamp": "2026-05-29T17:01:30Z",
        "tool_call_id": "call_abc123",
        "name": "ask_your_data",
    }
    fields.pop(missing_field)
    wire = _build([ChatMessage(**fields)])

    if missing_field == "tool_call_id":
        # Falls back to the message id so the linkage is at least
        # internally consistent (the LLM may not match it, but we
        # don't raise a validation error).
        assert wire[0]["tool_call_id"] == "t1"
    else:
        assert wire[0]["name"] == "tool"
