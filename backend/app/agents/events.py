"""
SSE event schema for the streaming agent conversation endpoint.

The agent runner emits a sequence of these events as it works through a
ReAct loop. The HTTP layer serializes them as text/event-stream frames so
the browser can render live progress (status, tool-call pills, optional
reasoning, the final message) instead of staring at a thinking indicator.

Events are deliberately *coarse* - one per LLM iteration / tool boundary,
not per token. Token-level streaming can layer in later via a reserved
``message_delta`` event without breaking this contract.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class StatusEvent(BaseModel):
    """Generic progress line displayed to the user (e.g. "Thinking...")."""

    type: Literal["status"] = "status"
    label: str
    elapsed_ms: Optional[int] = None


class ToolCallEvent(BaseModel):
    """Emitted just before a tool is executed.

    ``friendly_label`` is the user-facing copy ("Asking Genie...");
    ``args_summary`` is an optional short description of inputs that the
    UI may render under the pill (e.g. "What is our top revenue product?").
    ``arguments`` is the raw JSON-serializable payload the LLM produced
    for the call. The UI keeps a copy so it can reconstruct the
    assistant's ``tool_calls`` block on a continuation turn (chat
    completion APIs reject a ``tool`` message that isn't preceded by a
    matching assistant ``tool_calls``).
    """

    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    friendly_label: str
    args_summary: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None


class ToolResultEvent(BaseModel):
    """Emitted after a tool finishes execution.

    Skipped for tools that yield a ``pending_poll`` envelope - the poll
    lifecycle (handled by the UI) replaces the immediate result.

    ``result`` carries the raw, JSON-serializable payload the tool
    returned. The UI surfaces it in a collapsible "Raw output" panel
    underneath the pill so SAs / advanced users can see exactly what
    the agent saw — useful when debugging "why did the agent
    interpret that result this way?". Capped to
    ``AGENT_MAX_TOOL_OUTPUT_CHARS`` upstream (same cap that controls
    the LLM-facing copy) so we don't ship megabyte payloads through
    SSE.
    """

    type: Literal["tool_result"] = "tool_result"
    id: str
    name: str
    ok: bool
    summary: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Any] = None


class PendingPollEvent(BaseModel):
    """Tool returned an async handle the UI must poll to drain.

    ``kind`` identifies the polling protocol (e.g. "genie") so the UI
    knows which poll endpoint to call. ``ids`` carries whatever
    identifiers the poll endpoint needs (conversation_id, message_id,
    etc.). Generic by design so future async MCP servers reuse this.
    """

    type: Literal["pending_poll"] = "pending_poll"
    kind: str
    ids: Dict[str, Any]
    friendly_label: str
    tool_call_id: str
    tool_name: str


class ReasoningEvent(BaseModel):
    """Optional - only emitted when the LLM response carries reasoning.

    Surfaced in a collapsible "Thinking" disclosure above the answer.
    If the configured endpoint never produces reasoning, this event
    simply never fires.
    """

    type: Literal["reasoning"] = "reasoning"
    text: str


class MessageEvent(BaseModel):
    """Final assistant text for the turn."""

    type: Literal["message"] = "message"
    content: str


class RouteEvent(BaseModel):
    """Form-routing instruction extracted from the agent's response.

    The Self Service agent embeds a JSON code block (action="route_to_form")
    in its final message when it has gathered enough context to send the
    user to a pre-filled form. The streaming endpoint extracts that
    block, strips it from the visible message, and emits this event so
    the UI can render a "Continue to form" CTA without parsing markdown.
    """

    type: Literal["route"] = "route"
    path: str
    title: str
    prefill: Optional[Dict[str, Any]] = None


class DoneEvent(BaseModel):
    """Stream terminator. UI closes the reader on receipt."""

    type: Literal["done"] = "done"
    # Carry the full message history back so the caller can persist it
    # if needed - matches the legacy non-streaming response shape.
    messages: Optional[List[Dict[str, Any]]] = None
    # MLflow trace id for this turn (when tracing is enabled). The UI attaches
    # feedback to this id so a thumbs-up/down lands on the exact run.
    trace_id: Optional[str] = None


class ErrorEvent(BaseModel):
    """Recoverable or fatal failure. ``fatal=True`` => stream ends."""

    type: Literal["error"] = "error"
    message: str
    fatal: bool = False


# Discriminated union of every event the runner can emit. Useful for
# typing helpers and for the unit test that round-trips serialization.
AgentEvent = Union[
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
    PendingPollEvent,
    ReasoningEvent,
    MessageEvent,
    RouteEvent,
    DoneEvent,
    ErrorEvent,
]


def serialize_sse(event: AgentEvent) -> str:
    """Render a single event as a complete text/event-stream frame.

    Format follows the SSE spec: ``event: <type>\\ndata: <json>\\n\\n``.
    The terminating blank line is what tells parsers the frame is
    finished, so callers should not strip it.
    """
    payload = event.model_dump(mode="json", exclude_none=True)
    # ``type`` is duplicated as the SSE event field for parsers that
    # dispatch by event name, but we also keep it inside the JSON so a
    # generic data-only consumer can still discriminate.
    return f"event: {event.type}\ndata: {json.dumps(payload, default=str)}\n\n"


def parse_sse_frame(frame: str) -> Optional[Dict[str, Any]]:
    """Parse a single SSE frame back into a dict.

    Used in unit tests and as a reference for writing the matching
    frontend parser. Returns ``None`` for malformed frames rather
    than raising, since SSE consumers should be tolerant of keep-alive
    comments and partial data.
    """
    event_type: Optional[str] = None
    data_lines: List[str] = []
    for raw_line in frame.split("\n"):
        line = raw_line.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    if not data_lines:
        return None
    try:
        parsed = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return None
    if event_type and isinstance(parsed, dict):
        parsed.setdefault("type", event_type)
    return parsed


__all__ = [
    "AgentEvent",
    "StatusEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "PendingPollEvent",
    "ReasoningEvent",
    "MessageEvent",
    "RouteEvent",
    "DoneEvent",
    "ErrorEvent",
    "serialize_sse",
    "parse_sse_frame",
]
