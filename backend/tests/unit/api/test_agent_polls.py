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
    body = GeniePollRequest(conversation_id="c", message_id="m")
    parsed = _parse_genie_response(
        {"content": "still working", "structured": None, "is_error": False}, body
    )
    assert parsed.status == "running"


def test_parse_genie_response_text_json_completed():
    """Some Genie responses ship JSON in the text part instead of structured."""
    body = GeniePollRequest(conversation_id="conv-123", message_id="m")
    parsed = _parse_genie_response(
        {
            "content": '{"status": "COMPLETED", "answer": "ok"}',
            "structured": None,
            "is_error": False,
        },
        body,
    )
    assert parsed.status == "complete"
    assert parsed.result["answer"] == "ok"
    # conversation_id is echoed back from the request so the UI can
    # build a deep link even when the upstream payload omits it.
    assert parsed.result["conversation_id"] == "conv-123"


def test_parse_genie_response_includes_deep_link_when_payload_provides_one(monkeypatch):
    """Only surface a deep link when Genie itself supplied one — we
    used to synthesize URLs from ``DATABRICKS_HOST`` but the patterns
    we guessed (``/one#g/...``, ``/genie/rooms/{space}#conversation/{id}``)
    landed on the workspace home page instead of the conversation,
    which was worse UX than no link.
    """
    from app.api.v1 import agent_polls

    monkeypatch.setattr(
        agent_polls.settings,
        "DATABRICKS_HOST",
        "https://example.cloud.databricks.com",
        raising=False,
    )

    # Genie supplies a per-conversation URL on the payload itself.
    # Auth must be OBO — under SP we hide the link to avoid
    # "Conversation not found" landings.
    body = GeniePollRequest(
        space_id="space-abc", conversation_id="conv-1", message_id="m"
    )
    parsed = _parse_genie_response(
        {
            "structured": {
                "status": "COMPLETED",
                "conversation_url": (
                    "https://example.cloud.databricks.com/genie/rooms/space-abc/c/conv-1"
                ),
            },
            "is_error": False,
            "auth_source": "obo",
        },
        body,
    )
    assert parsed.status == "complete"
    assert parsed.result["_deep_link"] == (
        "https://example.cloud.databricks.com/genie/rooms/space-abc/c/conv-1"
    )
    assert parsed.result["_space_id"] == "space-abc"
    assert parsed.result["_auth_source"] == "obo"


def test_parse_genie_response_hides_deep_link_under_sp_fallback():
    """Local-dev SP fallback owns the conversation, so Databricks One
    won't show it in the human user's chat history. Surfacing a link
    that lands on "Conversation not found" is worse than no link.
    """
    body = GeniePollRequest(conversation_id="conv-7", message_id="m")
    parsed = _parse_genie_response(
        {
            "structured": {
                "status": "COMPLETED",
                "conversation_url": (
                    "https://example.cloud.databricks.com/one/chat/threads/conv-7"
                ),
            },
            "is_error": False,
            "auth_source": "sp",
        },
        body,
    )
    assert "_deep_link" not in parsed.result
    # Still echo the auth mode so the UI can render an explanatory
    # hint instead of leaving the user wondering where the button
    # went.
    assert parsed.result["_auth_source"] == "sp"


def test_parse_genie_response_omits_deep_link_when_no_url_in_payload(monkeypatch):
    """No URL in the payload ⇒ no button. Better than a dead link to
    the workspace root.
    """
    from app.api.v1 import agent_polls

    monkeypatch.setattr(
        agent_polls.settings,
        "DATABRICKS_HOST",
        "https://example.cloud.databricks.com",
        raising=False,
    )

    body = GeniePollRequest(conversation_id="conv-2", message_id="m")
    parsed = _parse_genie_response(
        {"structured": {"status": "COMPLETED"}, "is_error": False}, body
    )
    assert "_deep_link" not in parsed.result


def test_parse_genie_response_trusts_genie_supplied_urls(monkeypatch):
    """Whatever Genie hands us in a URL field is treated as
    authoritative. We previously had a guard that rejected URLs
    "looking like" the workspace home (``/one``, root) because earlier
    versions of this code synthesized those URLs ourselves and they
    didn't resolve — but the guard then rejected legitimate
    per-conversation links in customer environments. Genie is the
    authority on what URL to render, so we just pass it through if
    it's a Databricks-hosted http(s) URL.
    """
    body = GeniePollRequest(conversation_id="conv-x", message_id="m")
    parsed = _parse_genie_response(
        {
            "structured": {
                "status": "COMPLETED",
                "deep_link": "https://example.cloud.databricks.com/one",
            },
            "is_error": False,
            "auth_source": "obo",
        },
        body,
    )
    assert (
        parsed.result["_deep_link"]
        == "https://example.cloud.databricks.com/one"
    )


def test_query_has_rows_detects_shapes():
    from app.api.v1.agent_polls import _query_has_rows

    assert _query_has_rows({"statement_response": {"result": {"data_array": [[1]]}}})
    assert _query_has_rows({"result": {"data_typed_array": [[{"str": "x"}]]}})
    assert _query_has_rows({"rows": [[1, 2]]})
    assert not _query_has_rows({"query": "SELECT 1"})
    assert not _query_has_rows({"result": {"data_array": []}})


@pytest.mark.asyncio
async def test_maybe_fetch_attachment_rows_splices_in_result(monkeypatch):
    """A query attachment with no inline rows gets enriched via the SDK."""
    from app.api.v1 import agent_polls

    monkeypatch.setattr(
        agent_polls.settings, "DATABRICKS_HOST", "https://x.databricks.com", raising=False
    )
    monkeypatch.setattr(
        "app.providers.databricks_mcp.client.resolve_genie_bearer_token",
        lambda obo: ("tok", "obo"),
    )

    fetched = {"statement_response": {"result": {"data_array": [[1, 2]]},
                                      "manifest": {"schema": {"columns": [{"name": "a"}, {"name": "b"}]}}}}

    class _FakeGenie:
        def get_message_attachment_query_result(self, space, conv, msg, att):
            return SimpleNamespace(as_dict=lambda: fetched)

    class _FakeClient:
        def __init__(self, *a, **k):
            self.genie = _FakeGenie()

    monkeypatch.setattr("databricks.sdk.WorkspaceClient", _FakeClient, raising=False)

    result = {
        "status": "COMPLETED",
        "attachments": [
            {"attachment_id": "att-1", "query": {"query": "SELECT a, b FROM t"}},
        ],
    }
    body = GeniePollRequest(space_id="space-1", conversation_id="c", message_id="m")
    await agent_polls._maybe_fetch_attachment_rows(result, body, "obo-token")

    spliced = result["attachments"][0]["query"]["statement_response"]
    assert spliced["result"]["data_array"] == [[1, 2]]


@pytest.mark.asyncio
async def test_maybe_fetch_attachment_rows_noop_without_space(monkeypatch):
    """No space id ⇒ the SDK Genie result API can't be used; leave as-is."""
    from app.api.v1 import agent_polls

    called = {"n": 0}
    monkeypatch.setattr(
        "app.providers.databricks_mcp.client.resolve_genie_bearer_token",
        lambda obo: (called.__setitem__("n", called["n"] + 1), ("tok", "obo"))[1],
    )
    result = {"attachments": [{"attachment_id": "a", "query": {"query": "SELECT 1"}}]}
    body = GeniePollRequest(space_id=None, conversation_id="c", message_id="m")
    await agent_polls._maybe_fetch_attachment_rows(result, body, "obo-token")
    assert "statement_response" not in result["attachments"][0]["query"]
    assert called["n"] == 0


def test_parse_genie_response_finds_url_inside_attachments():
    """Some Genie payload shapes nest the share URL on an attachment."""
    body = GeniePollRequest(conversation_id="conv-y", message_id="m")
    parsed = _parse_genie_response(
        {
            "structured": {
                "status": "COMPLETED",
                "attachments": [
                    {
                        "text": {"content": "ok"},
                        "share_link": (
                            "https://example.cloud.databricks.com/"
                            "genie/rooms/abc/c/xyz"
                        ),
                    }
                ],
            },
            "is_error": False,
            "auth_source": "obo",
        },
        body,
    )
    assert parsed.result["_deep_link"] == (
        "https://example.cloud.databricks.com/genie/rooms/abc/c/xyz"
    )
