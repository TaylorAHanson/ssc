"""Unit tests for the ask_your_data tool wrapper around Genie MCP."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.providers.databricks_mcp import GenieAuthUnavailableError
from app.tools.data_chat.ask_genie import ask_your_data


@pytest.mark.asyncio
async def test_ask_your_data_no_auth_at_all_surfaces_actionable_error():
    """No OBO AND no SP fallback => return a clear, user-actionable error.

    The new auth path lets ``call_genie_tool`` itself try an SP OAuth
    fallback when there's no user OBO token. If that also fails (no
    DATABRICKS_CLIENT_ID/SECRET, etc.), it raises
    ``GenieAuthUnavailableError`` and we surface that to the user instead
    of crashing.
    """
    with patch(
        "app.tools.data_chat.ask_genie.call_genie_tool",
        new=AsyncMock(side_effect=GenieAuthUnavailableError("No authentication available for Databricks Genie...")),
    ):
        result = await ask_your_data.execute(question="how many customers?", space_id="space1")
    assert "error" in result
    assert "authentication" in result["error"].lower()


@pytest.mark.asyncio
async def test_ask_your_data_uses_general_genie_when_no_space_configured():
    """No space configured AND none passed => uses general Databricks Genie.

    The general Genie endpoint (``/api/2.0/mcp/genie``, no space suffix)
    is a valid target on its own; we should NOT bail with a "no space
    configured" error.
    """
    fake_response = {
        "content": "",
        "structured": {
            "conversation_id": "conv-1",
            "message_id": "msg-1",
            "status": "RUNNING",
        },
        "is_error": False,
    }
    with patch(
        "app.tools.data_chat.ask_genie._resolved_default_space_id", return_value=None
    ), patch(
        "app.tools.data_chat.ask_genie.call_genie_tool",
        new=AsyncMock(return_value=fake_response),
    ) as call_mock:
        result = await ask_your_data.execute(
            question="how many customers?",
            _obo_token="dummy-token",
        )
        assert "pending_poll" in result
        assert "error" not in result
        # space_id should NOT be passed to call_genie_tool when none is set.
        kwargs = call_mock.await_args.kwargs
        assert kwargs.get("space_id") is None
        # Envelope echoes the (absent) space so the poller can match.
        assert result["pending_poll"]["space_id"] is None


@pytest.mark.asyncio
async def test_ask_your_data_returns_pending_poll_envelope():
    """Happy path: returns a pending-poll envelope with conversation/message ids."""
    fake_response = {
        "content": "",
        "structured": {
            "conversation_id": "conv-123",
            "message_id": "msg-456",
            "status": "RUNNING",
        },
        "is_error": False,
    }

    with patch(
        "app.tools.data_chat.ask_genie.call_genie_tool",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await ask_your_data.execute(
            question="how many customers last quarter?",
            space_id="space1",
            _obo_token="dummy-token",
        )

    assert "pending_poll" in result
    pp = result["pending_poll"]
    assert pp["kind"] == "genie"
    assert pp["space_id"] == "space1"
    assert pp["conversation_id"] == "conv-123"
    assert pp["message_id"] == "msg-456"
    assert pp["question"] == "how many customers last quarter?"
    assert pp["friendly_label"] == "Asking Genie..."


@pytest.mark.asyncio
async def test_ask_your_data_accepts_response_id_handle():
    """Real Managed-MCP Genie returns the handle as ``response_id`` (sibling
    of ``conversation_id``), not ``message_id``. We must accept that
    spelling and normalize it onto our internal ``message_id`` slot.
    """
    fake_response = {
        "content": "",
        "structured": {
            "response_id": "1e068405aa504ceb939794eab7514f07",
            "conversation_id": "945eaf428d8848c3a94e8de89a56b2ea",
            "status": "in_progress",
        },
        "is_error": False,
    }
    with patch(
        "app.tools.data_chat.ask_genie.call_genie_tool",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await ask_your_data.execute(
            question="analyze marketing_data and sales_data for temporal trends",
            _obo_token="dummy-token",
        )

    assert "error" not in result, result
    assert "pending_poll" in result
    pp = result["pending_poll"]
    assert pp["conversation_id"] == "945eaf428d8848c3a94e8de89a56b2ea"
    assert pp["message_id"] == "1e068405aa504ceb939794eab7514f07"


@pytest.mark.asyncio
async def test_ask_your_data_handles_mcp_error():
    """If MCP reports an error, surface it as a tool error, not a poll."""
    fake_response = {
        "content": "Genie space is misconfigured",
        "structured": None,
        "is_error": True,
    }
    with patch(
        "app.tools.data_chat.ask_genie.call_genie_tool",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await ask_your_data.execute(
            question="anything",
            space_id="space1",
            _obo_token="t",
        )
    assert "pending_poll" not in result
    assert "error" in result
    assert "misconfigured" in result["error"]


@pytest.mark.asyncio
async def test_ask_your_data_handles_missing_query_handle():
    """If the response is healthy but missing IDs, error rather than poll forever."""
    fake_response = {
        "content": "<no structured content>",
        "structured": {"some_other_field": "x"},
        "is_error": False,
    }
    with patch(
        "app.tools.data_chat.ask_genie.call_genie_tool",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await ask_your_data.execute(
            question="anything",
            space_id="space1",
            _obo_token="t",
        )
    assert "pending_poll" not in result
    assert "error" in result
    assert "handle" in result["error"].lower()


@pytest.mark.asyncio
async def test_ask_your_data_friendly_label():
    """The tool's UX metadata should be 'Asking Genie...'."""
    assert ask_your_data.friendly_label == "Asking Genie..."
    assert ask_your_data.friendly_completion_label == "Genie answered."
