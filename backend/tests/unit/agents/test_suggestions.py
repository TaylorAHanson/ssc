"""Next-step suggestions: parsing the model's block, and carrying the result
through the runner, the stream route, history replay and the prompt."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from app.agents.events import DoneEvent, MessageEvent, parse_sse_frame, serialize_sse
from app.agents.suggestions import (
    MAX_SUGGESTION_CHARS,
    MAX_SUGGESTIONS,
    sanitize_suggestions,
    split_suggestions,
    with_suggestions_block,
)
from app.core.config import _yaml_config
from tests.unit.agents.test_runner_stream import _FakeLLMResponse, _make_runner


def _block(items) -> str:
    return f"```suggestions\n{json.dumps(items)}\n```"


@pytest.fixture
def suggestions_off(monkeypatch):
    monkeypatch.setitem(_yaml_config.setdefault("features", {}), "next_step_suggestions", False)


@pytest.fixture
def suggestions_on(monkeypatch):
    monkeypatch.setitem(_yaml_config.setdefault("features", {}), "next_step_suggestions", True)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_a_complete_block_is_removed_and_parsed():
    text = "The table is owned by data-eng.\n\n" + _block(["Find the table owner", "Show similar tables"])

    content, suggestions = split_suggestions(text)

    assert content == "The table is owned by data-eng."
    assert suggestions == ["Find the table owner", "Show similar tables"]


def test_text_without_a_block_comes_back_unchanged():
    text = "Here is a query:\n\n```sql\nSELECT 1\n```\n"

    assert split_suggestions(text) == (text, [])
    assert split_suggestions("") == ("", [])


def test_an_unterminated_block_is_removed_without_suggestions():
    content, suggestions = split_suggestions('Done.\n\n```suggestions\n["Request access", "Show sim')

    assert content == "Done."
    assert suggestions == []


def test_a_bare_opening_fence_is_removed():
    assert split_suggestions("Done.\n```suggestions") == ("Done.", [])


def test_malformed_json_is_ignored_quietly(caplog):
    caplog.set_level(logging.DEBUG, logger="app.agents.suggestions")

    content, suggestions = split_suggestions("Done.\n\n```suggestions\n[\"one\", two]\n```")

    assert content == "Done."
    assert suggestions == []
    assert any("malformed" in r.getMessage() for r in caplog.records)
    assert all(r.levelno <= logging.DEBUG for r in caplog.records)


def test_a_non_list_payload_is_ignored():
    assert split_suggestions('Done.\n```suggestions\n{"a": 1}\n```') == ("Done.", [])
    assert split_suggestions('Done.\n```suggestions\n"just text"\n```') == ("Done.", [])


def test_json_wrapped_in_stray_prose_still_parses():
    _, suggestions = split_suggestions('Ok\n```suggestions\nHere you go: ["Show similar tables"]\n```')

    assert suggestions == ["Show similar tables"]


def test_every_block_is_removed_and_the_last_one_wins():
    text = f"First.\n{_block(['Old offer'])}\nSecond.\n\n{_block(['New offer'])}"

    content, suggestions = split_suggestions(text)

    assert "```" not in content
    assert content.startswith("First.") and content.endswith("Second.")
    assert suggestions == ["New offer"]


def test_crlf_and_one_line_blocks_are_recognized():
    assert split_suggestions('Done.\r\n```suggestions\r\n["Yes, submit it"]\r\n```') == (
        "Done.",
        ["Yes, submit it"],
    )
    assert split_suggestions('Done. ```suggestions ["Yes, submit it"]```') == (
        "Done.",
        ["Yes, submit it"],
    )


def test_other_code_blocks_are_left_alone():
    route = '```json\n{"action": "route_to_form", "form_path": "/request/x"}\n```'
    content, suggestions = split_suggestions(f"Ready.\n{route}\n\n{_block(['Open the form'])}")

    assert route in content
    assert suggestions == ["Open the form"]


# ---------------------------------------------------------------------------
# Sanitizing
# ---------------------------------------------------------------------------
def test_too_many_suggestions_are_capped():
    items = [f"Option {i}" for i in range(10)]

    assert sanitize_suggestions(items) == items[:MAX_SUGGESTIONS]


def test_overlong_suggestions_are_dropped_not_truncated():
    long_one = "x" * (MAX_SUGGESTION_CHARS + 1)

    assert sanitize_suggestions([long_one, "Show similar tables"]) == ["Show similar tables"]


def test_duplicates_empties_and_non_text_are_dropped():
    raw = ["Find the owner", "find the OWNER", "  ", "", None, 3, ["nested"], "Show tables"]

    assert sanitize_suggestions(raw) == ["Find the owner", "Show tables"]


def test_items_are_reduced_to_plain_text():
    assert sanitize_suggestions([
        "**Request access** to `ws-supplychain-prod`",
        "- Show similar tables",
        "1. Find the table owner",
        '"Yes, submit it"',
    ]) == [
        "Request access to ws-supplychain-prod",
        "Show similar tables",
        "Find the table owner",
        "Yes, submit it",
    ]
    assert sanitize_suggestions([
        "Open [the form](https://example.com/form)",
        "Line one\nline two",
        "<b>Change</b> a detail",
    ]) == [
        "Open the form",
        "Line one line two",
        "Change a detail",
    ]


def test_items_carrying_a_url_are_dropped():
    assert sanitize_suggestions(["Visit https://evil.example", "Go to www.x.io", "Keep me"]) == ["Keep me"]


def test_identifiers_and_comparisons_survive():
    assert sanitize_suggestions(["Describe main.sales.daily_orders", "Rows < 100 and > 5"]) == [
        "Describe main.sales.daily_orders",
        "Rows < 100 and > 5",
    ]


def test_object_items_use_their_text_field():
    assert sanitize_suggestions([{"text": "Show tables"}, {"label": "Find owner"}, {"x": 1}]) == [
        "Show tables",
        "Find owner",
    ]


def test_a_bare_string_is_not_treated_as_a_list():
    assert sanitize_suggestions("Show tables") == []
    assert sanitize_suggestions(None) == []


# ---------------------------------------------------------------------------
# History replay
# ---------------------------------------------------------------------------
def test_replayed_turns_get_one_canonical_block(suggestions_on):
    assert with_suggestions_block("Hello", ["One", "Two"]) == 'Hello\n\n```suggestions\n["One", "Two"]\n```'


def test_a_stale_raw_block_is_replaced_not_duplicated(suggestions_on):
    replayed = with_suggestions_block("Hello\n\n" + _block(["Stale"]), ["Fresh"])

    assert replayed.count("```suggestions") == 1
    assert "Stale" not in replayed and "Fresh" in replayed


def test_turns_without_suggestions_replay_as_prose(suggestions_on):
    assert with_suggestions_block("Hello", None) == "Hello"
    assert with_suggestions_block("Hello\n\n" + _block(["Stale"]), []) == "Hello"
    assert with_suggestions_block("", None) == ""


def test_with_the_feature_off_history_is_prose_only(suggestions_off):
    assert with_suggestions_block("Hello", ["One"]) == "Hello"


def test_client_replayed_history_carries_the_offered_suggestions(suggestions_on):
    from app.api.v1.agent import ChatMessage, ConversationRequest, _resolve_history

    request = ConversationRequest(
        query="Find the table owner",
        conversation_history=[
            ChatMessage(id="u1", type="user", content="Who owns sales?", timestamp="t"),
            ChatMessage(
                id="a1",
                type="agent",
                content="It's main.sales.",
                timestamp="t",
                suggestions=["Find the table owner"],
            ),
        ],
    )

    history = _resolve_history(request, SimpleNamespace(email="a@example.com"), None)

    assert history[0]["content"] == "Who owns sales?"
    assert history[1]["content"] == 'It\'s main.sales.\n\n```suggestions\n["Find the table owner"]\n```'


def test_stored_transcripts_carry_the_offered_suggestions(suggestions_on):
    from app.services import chat_session_service as svc

    history = svc.transcript_to_history([
        {"id": "u1", "kind": "user", "content": "hi"},
        {"id": "a1", "kind": "agent", "content": "hello", "suggestions": ["Show tables"]},
    ])

    assert history[1]["content"] == 'hello\n\n```suggestions\n["Show tables"]\n```'


# ---------------------------------------------------------------------------
# Runner + events
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_runner_strips_the_block_and_emits_suggestions(suggestions_on):
    reply = "Found it.\n\n" + _block(["Find the table owner", "Show similar tables"])
    runner = _make_runner([], [_FakeLLMResponse(content=reply)])

    events = [ev async for ev in runner.run_stream(query="hi")]

    message = next(e for e in events if isinstance(e, MessageEvent))
    assert message.content == "Found it."
    assert message.suggestions == ["Find the table owner", "Show similar tables"]
    assert isinstance(events[-1], DoneEvent)


@pytest.mark.asyncio
async def test_with_the_feature_off_the_block_is_still_stripped(suggestions_off):
    runner = _make_runner([], [_FakeLLMResponse(content="Found it.\n\n" + _block(["Show tables"]))])

    events = [ev async for ev in runner.run_stream(query="hi")]

    message = next(e for e in events if isinstance(e, MessageEvent))
    assert message.content == "Found it."
    assert message.suggestions is None


@pytest.mark.asyncio
async def test_the_non_streaming_shim_returns_suggestions(suggestions_on):
    runner = _make_runner([], [_FakeLLMResponse(content="Ok.\n" + _block(["Yes, submit it"]))])

    result = await runner.run(query="hi")

    assert result["content"] == "Ok."
    assert result["suggestions"] == ["Yes, submit it"]


def test_suggestions_are_serialized_only_when_present():
    plain = parse_sse_frame(serialize_sse(MessageEvent(content="hi")))
    offered = parse_sse_frame(serialize_sse(MessageEvent(content="hi", suggestions=["Show tables"])))

    assert "suggestions" not in plain
    assert offered["suggestions"] == ["Show tables"]


@pytest.mark.asyncio
async def test_the_route_rewrite_keeps_the_suggestions(monkeypatch):
    from app.api.v1 import agent as agent_api

    route = '```json\n{"action": "route_to_form", "form_path": "/request/data-access"}\n```'

    class _Runner:
        async def run_stream(self, **_kwargs):
            yield MessageEvent(content=f"Ready.\n\n{route}", suggestions=["Open the form"])
            yield DoneEvent()

    async def _build(*_args, **_kwargs):
        return _Runner(), [], "unified"

    monkeypatch.setattr(agent_api, "_build_runner_and_history", _build)

    response = await agent_api.stream_conversation(
        agent_api.ConversationRequest(query="go"),
        SimpleNamespace(state=SimpleNamespace(token=None)),
        db=None,
        current_user=SimpleNamespace(email="a@example.com"),
    )
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
    frames = [parse_sse_frame(f + "\n\n") for f in body.strip().split("\n\n")]

    route_frame = next(f for f in frames if f["type"] == "route")
    message = next(f for f in frames if f["type"] == "message")
    assert route_frame["path"] == "/request/data-access"
    assert message["content"] == "Ready."
    assert message["suggestions"] == ["Open the form"]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["unified", "authoring"])
def test_the_prompt_asks_for_suggestions_when_enabled(suggestions_on, mode):
    from app.agents.prompts import NEXT_STEP_SUGGESTIONS_INSTRUCTIONS, get_agent_prompt

    prompt = get_agent_prompt(tools_override=[], mode=mode)

    assert NEXT_STEP_SUGGESTIONS_INSTRUCTIONS in prompt
    assert prompt.rstrip().endswith(NEXT_STEP_SUGGESTIONS_INSTRUCTIONS.rstrip())


@pytest.mark.parametrize("mode", ["unified", "authoring"])
def test_the_prompt_omits_suggestions_when_disabled(suggestions_off, mode):
    from app.agents.prompts import get_agent_prompt

    assert "```suggestions" not in get_agent_prompt(tools_override=[], mode=mode)


def test_the_prompt_example_parses_with_the_runtime_parser():
    from app.agents.prompts import NEXT_STEP_SUGGESTIONS_INSTRUCTIONS

    _, suggestions = split_suggestions(NEXT_STEP_SUGGESTIONS_INSTRUCTIONS)

    assert 2 <= len(suggestions) <= MAX_SUGGESTIONS
