"""Unit tests for the SSE event schema."""
from __future__ import annotations

import pytest

from app.agents.events import (
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    ReasoningEvent,
    RouteEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
    parse_sse_frame,
    serialize_sse,
)


def test_status_event_serialize_roundtrip():
    ev = StatusEvent(label="Thinking...", elapsed_ms=42)
    frame = serialize_sse(ev)
    assert frame.startswith("event: status\n")
    assert frame.endswith("\n\n")
    parsed = parse_sse_frame(frame)
    assert parsed == {"type": "status", "label": "Thinking...", "elapsed_ms": 42}


def test_tool_call_event_omits_optional_fields_when_unset():
    ev = ToolCallEvent(id="abc", name="find_owner", friendly_label="Finding the owner...")
    frame = serialize_sse(ev)
    parsed = parse_sse_frame(frame)
    assert parsed is not None
    assert parsed["type"] == "tool_call"
    assert parsed["id"] == "abc"
    assert parsed["friendly_label"] == "Finding the owner..."
    # exclude_none is set, so args_summary should be absent.
    assert "args_summary" not in parsed


def test_tool_result_event_records_failure():
    ev = ToolResultEvent(id="x", name="t", ok=False, error="kaboom")
    parsed = parse_sse_frame(serialize_sse(ev))
    assert parsed["ok"] is False
    assert parsed["error"] == "kaboom"


def test_reasoning_message_done_error_events():
    for ev in [
        ReasoningEvent(text="thinking..."),
        MessageEvent(content="<p>final</p>"),
        DoneEvent(),
        ErrorEvent(message="bad", fatal=True),
    ]:
        parsed = parse_sse_frame(serialize_sse(ev))
        assert parsed is not None
        assert parsed["type"] == ev.type


def test_route_event_carries_path_and_title():
    ev = RouteEvent(path="/training", title="Training")
    parsed = parse_sse_frame(serialize_sse(ev))
    assert parsed is not None
    assert parsed["type"] == "route"
    assert parsed["path"] == "/training"
    assert parsed["title"] == "Training"


def test_parse_sse_frame_ignores_keepalive_comments():
    raw = ":\nevent: status\ndata: {\"type\":\"status\",\"label\":\"hi\"}\n\n"
    parsed = parse_sse_frame(raw)
    assert parsed == {"type": "status", "label": "hi"}


def test_parse_sse_frame_returns_none_for_malformed():
    assert parse_sse_frame("data: {not json") is None
    assert parse_sse_frame(": just a comment") is None


@pytest.mark.parametrize(
    "ev",
    [
        StatusEvent(label="hello"),
        ToolCallEvent(id="i", name="n", friendly_label="L"),
        ToolResultEvent(id="i", name="n", ok=True),
        DoneEvent(messages=[{"role": "user", "content": "hi"}]),
    ],
)
def test_serialize_emits_event_field_matching_type(ev):
    frame = serialize_sse(ev)
    first_line = frame.split("\n", 1)[0]
    assert first_line == f"event: {ev.type}"
