"""Unit tests for the Genie poll endpoint.

We test the request-handling logic by directly invoking the route
function with mocked MCP calls; we don't spin up FastAPI for these.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.agent_polls import (
    GeniePollRequest,
    _parse_genie_response,
    poll_genie,
)
from app.providers.databricks_mcp import GenieAuthUnavailableError


def _fake_request_with_token(token: str | None = "user-obo-token"):
    """Build a tiny stand-in for FastAPI's Request with a state.token."""
    state = SimpleNamespace()
    if token is not None:
        state.token = token
    return SimpleNamespace(state=state)


def _fake_user(email: str = "user@example.com"):
    return SimpleNamespace(email=email, roles=["User"], entitlements=[])


@pytest.mark.asyncio
async def test_poll_genie_complete_with_structured_payload():
    fake_response = {
        "content": "",
        "structured": {
            "status": "COMPLETED",
            "answer": "There were 1234 customers.",
            "rows": [{"customer_count": 1234}],
        },
        "is_error": False,
    }
    body = GeniePollRequest(
        space_id="s",
        conversation_id="c",
        message_id="m",
        question="how many customers?",
    )
    with patch(
        "app.api.v1.agent_polls.call_genie_tool",
        new=AsyncMock(return_value=fake_response),
    ) as mock_call:
        result = await poll_genie(body, _fake_request_with_token(), _fake_user())
    assert result.status == "complete"
    assert result.result["answer"] == "There were 1234 customers."
    assert result.attempt_after_ms is None
    # The wire shape carries the handle as ``message_id`` for protocol
    # consistency, but the Managed-MCP Genie tool expects it as
    # ``response_id``. The poll endpoint must translate at this boundary.
    sent_args = mock_call.await_args.kwargs["arguments"]
    assert sent_args == {"conversation_id": "c", "response_id": "m"}


@pytest.mark.asyncio
async def test_poll_genie_running_yields_running_status():
    fake_response = {
        "content": "",
        "structured": {"status": "RUNNING"},
        "is_error": False,
    }
    body = GeniePollRequest(space_id="s", conversation_id="c", message_id="m")
    with patch(
        "app.api.v1.agent_polls.call_genie_tool",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await poll_genie(body, _fake_request_with_token(), _fake_user())
    assert result.status == "running"
    assert result.attempt_after_ms is not None
    assert result.attempt_after_ms > 0


@pytest.mark.asyncio
async def test_poll_genie_failed_status_propagates_error():
    fake_response = {
        "content": "",
        "structured": {
            "status": "FAILED",
            "error": "permission denied on table foo",
        },
        "is_error": False,
    }
    body = GeniePollRequest(space_id="s", conversation_id="c", message_id="m")
    with patch(
        "app.api.v1.agent_polls.call_genie_tool",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await poll_genie(body, _fake_request_with_token(), _fake_user())
    assert result.status == "failed"
    assert "permission denied" in (result.error or "")


@pytest.mark.asyncio
async def test_poll_genie_no_auth_at_all_returns_401():
    """No OBO header AND no SP fallback => 401 with the auth message.

    The kickoff would normally have established auth, but if a downstream
    poll lands on a fresh process with no OBO header *and* the SP fallback
    fails (missing client creds, etc.), we surface a 401 so the UI can
    show the message verbatim.
    """
    from fastapi import HTTPException

    body = GeniePollRequest(space_id="s", conversation_id="c", message_id="m")
    with patch(
        "app.api.v1.agent_polls.call_genie_tool",
        new=AsyncMock(side_effect=GenieAuthUnavailableError("No authentication available for Databricks Genie...")),
    ):
        with pytest.raises(HTTPException) as exc:
            await poll_genie(body, _fake_request_with_token(token=None), _fake_user())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_poll_genie_handles_mcp_exception():
    body = GeniePollRequest(space_id="s", conversation_id="c", message_id="m")
    with patch(
        "app.api.v1.agent_polls.call_genie_tool",
        new=AsyncMock(side_effect=RuntimeError("network blew up")),
    ):
        result = await poll_genie(body, _fake_request_with_token(), _fake_user())
    assert result.status == "failed"
    assert "network blew up" in (result.error or "")


def test_parse_genie_response_text_only_payload_treated_as_running():
    """A response that's just unparseable text => keep polling."""
    parsed = _parse_genie_response({"content": "still working", "structured": None, "is_error": False})
    assert parsed.status == "running"


def test_parse_genie_response_text_json_completed():
    """Some Genie responses ship JSON in the text part instead of structured."""
    parsed = _parse_genie_response(
        {
            "content": '{"status": "COMPLETED", "answer": "ok"}',
            "structured": None,
            "is_error": False,
        }
    )
    assert parsed.status == "complete"
    assert parsed.result["answer"] == "ok"
