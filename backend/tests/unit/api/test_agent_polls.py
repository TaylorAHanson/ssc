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


# --------------------------------------------------------------------------- incomplete


def test_parse_genie_response_incomplete_with_an_answer_is_terminal():
    """``incomplete`` is a terminal status: keep what Genie produced, stop polling."""
    parsed = _parse_genie_response(
        {
            "content": "",
            "structured": {"status": "incomplete", "final_answer": "Partial: 37 managed tables so far."},
            "is_error": False,
        },
        GeniePollRequest(conversation_id="c", message_id="m"),
    )
    assert parsed.status == "complete"
    assert parsed.result["final_answer"].startswith("Partial")
    assert parsed.attempt_after_ms is None


def test_parse_genie_response_incomplete_with_nothing_fails_plainly():
    parsed = _parse_genie_response(
        {"content": "", "structured": {"status": "incomplete"}, "is_error": False},
        GeniePollRequest(conversation_id="c", message_id="m"),
    )
    assert parsed.status == "failed"
    assert "before it could finish" in parsed.error
    assert parsed.attempt_after_ms is None


# ------------------------------------------------------------------- query_items rows


def _items_result(*item_ids: str) -> dict:
    return {
        "final_answer": "There are 38 tables.",
        "query_items": [{"item_id": i, "sql": f"SELECT {n}"} for n, i in enumerate(item_ids, 1)],
    }


def _query_result(rows, *, ready=True, state="SUCCEEDED", truncated=False):
    return {
        "content": "",
        "is_error": False,
        "structured": {
            "statement_state": state,
            "ready": ready,
            "columns": [{"name": "table_type", "type_text": "STRING"}, {"name": "n", "type_text": "BIGINT"}],
            "rows": rows,
            "total_row_count": len(rows),
            "truncated": truncated,
        },
    }


@pytest.mark.asyncio
async def test_query_item_rows_are_fetched_and_shaped_for_the_chat():
    from app.api.v1.agent_polls import _maybe_fetch_query_item_rows

    result = _items_result("item-1")
    body = GeniePollRequest(conversation_id="conv", message_id="resp")
    mock = AsyncMock(return_value=_query_result([["MANAGED", "37"], ["VIEW", "1"]]))
    with patch("app.api.v1.agent_polls.call_genie_tool", new=mock):
        await _maybe_fetch_query_item_rows(result, body, "tok")

    # Asked under the names the MCP tool expects, with no space for general Genie.
    assert mock.await_args.kwargs["tool_name"] == "genie_get_query_result"
    assert mock.await_args.kwargs["arguments"] == {
        "conversation_id": "conv",
        "response_id": "resp",
        "item_id": "item-1",
    }
    assert mock.await_args.kwargs["space_id"] is None
    (attachment,) = result["attachments"]
    assert attachment["attachment_id"] == "item-1"
    assert attachment["query"]["query"] == "SELECT 1"
    assert attachment["query"]["result"]["rows"] == [["MANAGED", "37"], ["VIEW", "1"]]
    assert attachment["query"]["result"]["columns"][0]["name"] == "table_type"
    assert attachment["query"]["result"]["total_row_count"] == 2
    # The shape the chat already tables and charts from.
    from app.api.v1.agent_polls import _query_has_rows

    assert _query_has_rows(attachment["query"])


@pytest.mark.asyncio
async def test_query_item_rows_waits_for_a_result_that_is_not_ready_yet():
    from app.api.v1 import agent_polls

    result = _items_result("item-1")
    body = GeniePollRequest(conversation_id="conv", message_id="resp")
    mock = AsyncMock(
        side_effect=[
            _query_result([], ready=False, state="RUNNING"),
            _query_result([["MANAGED", "37"]]),
        ]
    )
    with patch("app.api.v1.agent_polls.call_genie_tool", new=mock), patch.object(
        agent_polls, "_RESULT_READY_DELAY_SECONDS", 0
    ):
        await agent_polls._maybe_fetch_query_item_rows(result, body, None)
    assert mock.await_count == 2
    assert result["attachments"][0]["query"]["result"]["rows"] == [["MANAGED", "37"]]


@pytest.mark.asyncio
async def test_query_item_rows_skips_failed_statements_and_errors():
    from app.api.v1.agent_polls import _maybe_fetch_query_item_rows

    result = _items_result("bad-state", "errored")
    body = GeniePollRequest(conversation_id="conv", message_id="resp")
    mock = AsyncMock(
        side_effect=[
            _query_result([], state="FAILED"),
            {"content": "boom", "structured": None, "is_error": True},
        ]
    )
    with patch("app.api.v1.agent_polls.call_genie_tool", new=mock):
        await _maybe_fetch_query_item_rows(result, body, None)
    # Nothing fetched: the text answer stands on its own and is not decorated.
    assert "attachments" not in result


@pytest.mark.asyncio
async def test_query_item_rows_limits_fetches_and_skips_attachments_already_present():
    from app.api.v1 import agent_polls

    ids = [f"item-{n}" for n in range(1, 7)]
    result = _items_result(*ids)
    result["attachments"] = [{"attachment_id": "item-1", "query": {"query": "SELECT 1", "result": {"rows": [[1]]}}}]
    body = GeniePollRequest(conversation_id="conv", message_id="resp")
    mock = AsyncMock(return_value=_query_result([["MANAGED", "37"]]))
    with patch("app.api.v1.agent_polls.call_genie_tool", new=mock):
        await agent_polls._maybe_fetch_query_item_rows(result, body, None)
    # item-1 is already there; the cap applies to the first three items overall.
    assert [c.kwargs["arguments"]["item_id"] for c in mock.await_args_list] == ["item-2", "item-3"]
    assert len(result["attachments"]) == 3


@pytest.mark.asyncio
async def test_poll_genie_completed_answer_gets_rows_from_the_result_tool():
    """End to end: a completed general-Genie poll comes back with a chartable query result."""
    completed = {
        "content": "",
        "is_error": False,
        "structured": {"status": "completed", **_items_result("item-1")},
    }

    async def fake_call(*, tool_name, **_kwargs):
        if tool_name == "genie_poll_response":
            return completed
        assert tool_name == "genie_get_query_result"
        return _query_result([["MANAGED", "37"]])

    body = GeniePollRequest(conversation_id="conv", message_id="resp")
    with patch("app.api.v1.agent_polls.call_genie_tool", new=fake_call):
        out = await poll_genie(body, _fake_request_with_token(), _fake_user())
    assert out.status == "complete"
    assert out.result["attachments"][0]["query"]["result"]["rows"] == [["MANAGED", "37"]]


# --------------------------------------------------------- "not found" right after kickoff
NOT_FOUND = (
    "Your input couldn't be processed: polling Genie response failed with HTTP 404: "
    "response_id r-1 not found in conversation c-1. Check the conversation/response IDs "
    "and question wording, then try again."
)


@pytest.fixture
def not_found_window():
    """The per-process record of "not found" polls, emptied around each test."""
    from app.api.v1 import agent_polls

    agent_polls._not_found_since.clear()
    yield agent_polls._not_found_since
    agent_polls._not_found_since.clear()


async def _poll(responses, body=None):
    queue = list(responses)

    async def fake_call(**_kwargs):
        return queue.pop(0)

    body = body or GeniePollRequest(conversation_id="c-1", message_id="r-1")
    with patch("app.api.v1.agent_polls.call_genie_tool", new=fake_call):
        return await poll_genie(body, _fake_request_with_token(), _fake_user())


def _error(content=NOT_FOUND):
    return {"content": content, "structured": None, "is_error": True}


@pytest.mark.asyncio
async def test_a_response_not_found_just_after_kickoff_keeps_polling(not_found_window):
    """Genie can report a response it just created as missing for a moment; the answer
    is there on the next poll, so the chat must not end on that first 404."""
    out = await _poll([_error()])
    assert out.status == "running"
    assert out.attempt_after_ms == 1500
    assert out.error is None


@pytest.mark.asyncio
async def test_a_response_that_stays_missing_fails_once_the_window_has_passed(not_found_window):
    import time

    from app.api.v1 import agent_polls

    await _poll([_error()])
    # The first "not found" for this response was seen long enough ago.
    key = ("c-1", "r-1")
    not_found_window[key] = time.monotonic() - agent_polls._NOT_FOUND_GRACE_SECONDS - 1
    out = await _poll([_error()])
    assert out.status == "failed"
    assert "not found in conversation" in (out.error or "")


@pytest.mark.asyncio
async def test_a_found_response_clears_its_window(not_found_window):
    await _poll([_error()])
    assert ("c-1", "r-1") in not_found_window
    done = {"content": "", "structured": {"status": "COMPLETED", "final_answer": "ok"}, "is_error": False}
    assert (await _poll([done])).status == "complete"
    # Nothing is left behind once the answer arrives.
    assert not_found_window == {}


@pytest.mark.asyncio
async def test_each_response_has_its_own_window(not_found_window):
    import time

    from app.api.v1 import agent_polls

    not_found_window[("c-1", "old")] = time.monotonic() - agent_polls._NOT_FOUND_GRACE_SECONDS - 1
    out = await _poll([_error()], GeniePollRequest(conversation_id="c-1", message_id="new"))
    assert out.status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "Genie reported an error.",
        "HTTP 500: internal error",
        "Permission denied: not found in your workspace",  # "not found" alone is not a missing response
        "",
    ],
)
async def test_other_errors_still_fail_immediately(not_found_window, content):
    out = await _poll([_error(content)])
    assert out.status == "failed"
    assert not_found_window == {}


def test_stale_windows_are_forgotten(not_found_window):
    import time

    from app.api.v1 import agent_polls

    not_found_window[("c", "gone")] = time.monotonic() - agent_polls._NOT_FOUND_FORGET_SECONDS - 1
    agent_polls._within_not_found_grace(GeniePollRequest(conversation_id="c", message_id="fresh"))
    assert ("c", "gone") not in not_found_window and ("c", "fresh") in not_found_window
