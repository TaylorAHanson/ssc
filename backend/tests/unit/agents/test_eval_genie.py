"""Asking Genie through the assistant test library.

Genie answers outside the agent loop: the agent hands the browser a conversation to
poll, the browser shows Genie's text as-is, and replays the exchange (with its
conversation id) on the next turn. The runner plays the browser's part, and these
tests pin that it does so faithfully. No model and no Genie are called.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agents.eval import checks, library, runner
from app.agents.eval.library import EvalCase, LibraryError, ToolArgExpectation, parse_suite
from app.api.v1.agent_polls import GeniePollResponse

IDS = {"conversation_id": "conv-1", "message_id": "msg-1", "question": "top customers?"}

RESULT = {
    "final_answer": "Customer A leads.",
    "conversation_id": "conv-1",
    "query_items": [{"item_id": "i1", "sql": "SELECT c_name FROM customer"}],
    "attachments": [
        {
            "attachment_id": "i1",
            "query": {
                "query": "SELECT c_name FROM customer",
                "result": {
                    "columns": [{"name": "c_name", "type_text": "STRING"}, "total"],
                    "rows": [["A", "9"], ["B", "8"]],
                    "total_row_count": 2,
                },
            },
        }
    ],
}


def _case(**overrides) -> EvalCase:
    base = dict(id="g", suite="s", name="g", turns=["ask genie"], expected="ok")
    base.update(overrides)
    return EvalCase(**base)


def _complete(result=None) -> GeniePollResponse:
    return GeniePollResponse(status="complete", result=RESULT if result is None else result)


# ----------------------------------------------------------------- the library
def _suite(**case):
    base = {"id": "c", "question": "q", "expected": "e"}
    base.update(case)
    return {"suite": "s", "vars": {"v": "Customer"}, "cases": [base]}


def test_requires_tools_and_tool_args_are_parsed():
    plain = parse_suite(_suite()).cases[0]
    assert plain.requires_tools == [] and plain.expect_tool_args == []

    case = parse_suite(
        _suite(
            requires_tools=["ask_your_data"],
            expect_tool_args=[
                {"tool": "ask_your_data", "arg": "conversation_id", "turn": 2, "matches": "\\S"},
                {"tool": "ask_your_data", "arg": "question", "not_matches": "(?i)select", "matches": "${v}"},
            ],
        )
    ).cases[0]
    assert case.requires_tools == ["ask_your_data"]
    assert case.expect_tool_args[0] == ToolArgExpectation(
        tool="ask_your_data", arg="conversation_id", matches="\\S", turn=2
    )
    # Variables render in what a call must match, like every other pattern in a case.
    assert case.expect_tool_args[1].matches == "Customer"


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"arg": "x", "matches": "a"}, "needs 'tool' and 'arg'"),
        ({"tool": "t", "arg": "x"}, "needs 'matches' or 'not_matches'"),
        ({"tool": "t", "arg": "x", "matches": "("}, "bad pattern"),
        ({"tool": "t", "arg": "x", "matches": "a", "turn": 0}, "counts from 1"),
        ({"tool": "t", "arg": "x", "matches": "a", "turn": "two"}, "whole number"),
    ],
)
def test_a_malformed_tool_arg_expectation_is_an_error_not_a_silent_pass(entry, message):
    with pytest.raises(LibraryError, match=message):
        parse_suite(_suite(expect_tool_args=[entry]))


def test_the_shipped_genie_suite_acts_as_the_caller_and_names_real_tools():
    from app.tools import catalog

    known = {name for name, _tool, _origin in catalog.all_tools()}
    suite = next(s for s in library.load_suites() if s.name == "genie")
    assert suite.cases
    for case in suite.cases:
        assert case.needs_identity, f"{case.id}: Genie runs as the caller"
        named = {e.tool for e in case.expect_tool_args}
        assert not named - known, f"{case.id}: expect_tool_args names unknown tools {named - known}"
    # The ones that need Genie say so, so they skip where it is switched off.
    needs_genie = {c.id for c in suite.cases if "ask_your_data" in c.requires_tools}
    assert {"genie-ask", "genie-followup", "genie-chart"} <= needs_genie


# ------------------------------------------------------------------ the checks
def _calls(*calls):
    return [{"name": n, "arguments": a, "turn": t} for n, a, t in calls]


def test_a_follow_up_must_pass_the_conversation_id():
    case = _case(
        expect_tool_args=[ToolArgExpectation("ask_your_data", "conversation_id", matches="\\S", turn=2)]
    )
    ok = _calls(("ask_your_data", {"question": "q"}, 1), ("ask_your_data", {"question": "q2", "conversation_id": "c1"}, 2))
    lost = _calls(("ask_your_data", {"question": "q"}, 1), ("ask_your_data", {"question": "q2"}, 2))

    assert all(c["ok"] for c in checks.evaluate_checks(case, [c["name"] for c in ok], "", ok))
    failed = checks.failed_checks(checks.evaluate_checks(case, [c["name"] for c in lost], "", lost))
    assert len(failed) == 1 and "turn 2" in failed[0]["check"] and "<not passed>" in failed[0]["detail"]


def test_a_question_rewritten_as_sql_fails_the_check():
    case = _case(expect_tool_args=[ToolArgExpectation("ask_your_data", "question", not_matches="(?i)\\bselect\\b")])
    sql = _calls(("ask_your_data", {"question": "SELECT * FROM t"}, 1))
    plain = _calls(("ask_your_data", {"question": "top customers"}, 1))
    assert checks.failed_checks(checks.evaluate_checks(case, ["ask_your_data"], "", sql))
    assert not checks.failed_checks(checks.evaluate_checks(case, ["ask_your_data"], "", plain))


def test_every_matching_call_must_satisfy_the_expectation():
    case = _case(expect_tool_args=[ToolArgExpectation("ask_your_data", "question", not_matches="(?i)select")])
    mixed = _calls(("ask_your_data", {"question": "fine"}, 1), ("ask_your_data", {"question": "select 1"}, 2))
    failed = checks.failed_checks(checks.evaluate_checks(case, ["ask_your_data"] * 2, "", mixed))
    assert len(failed) == 1 and "1 of 2" in failed[0]["detail"]


def test_an_expectation_on_a_call_that_never_happened_fails():
    case = _case(expect_tool_args=[ToolArgExpectation("ask_your_data", "question", matches="x", turn=2)])
    only_first = _calls(("ask_your_data", {"question": "x"}, 1))
    failed = checks.failed_checks(checks.evaluate_checks(case, ["ask_your_data"], "", only_first))
    assert failed and "not called on turn 2" in failed[0]["detail"]
    # Callers that pass no call details cannot satisfy it either; it never passes by omission.
    assert checks.failed_checks(checks.evaluate_checks(case, [], ""))


def test_non_text_arguments_are_matched_as_json():
    case = _case(expect_tool_args=[ToolArgExpectation("t", "ids", matches="\\b42\\b")])
    calls = _calls(("t", {"ids": [41, 42]}, 1))
    assert not checks.failed_checks(checks.evaluate_checks(case, ["t"], "", calls))


# ----------------------------------------------------------- tools switched off
def test_a_case_needing_a_tool_the_role_lacks_is_skipped_not_failed():
    async def boom(*_a, **_k):
        raise AssertionError("agent must not run")

    case = _case(requires_tools=["ask_your_data"])
    res = asyncio.run(runner.run_case(case, tools=[SimpleNamespace(name="run_sql")], agent=boom))
    assert res["status"] == "skipped" and "ask_your_data" in res["error"]

    async def agent(case, **_kw):
        return {"answer": "ok", "tool_calls": [], "transcript": []}

    async def judge(case, **_kw):
        return {"verdict": "pass", "score": 90, "rationale": "r", "missing": []}

    res = asyncio.run(
        runner.run_case(
            case, tools=[SimpleNamespace(name="ask_your_data")], agent=agent, judge_fn=judge
        )
    )
    assert res["status"] == "pass"


# -------------------------------------------------------------- polling Genie
def _drain(responses, **kw):
    """Poll against a scripted sequence of poll responses; returns (outcome, sleeps)."""
    queue = list(responses)
    sleeps = []

    async def poll(body, request, user):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(seconds):
        sleeps.append(seconds)

    outcome = asyncio.run(
        runner.drain_genie_poll(
            IDS, obo_token="tok", email="me@example.com", role="User", poll_fn=poll, sleep=sleep, **kw
        )
    )
    return outcome, sleeps


def test_polling_waits_until_genie_settles_and_honours_the_suggested_pace():
    running = GeniePollResponse(status="running", attempt_after_ms=2000)
    outcome, sleeps = _drain([running, running, _complete()])
    assert outcome == {"status": "complete", "result": RESULT}
    assert sleeps == [2.0, 2.0]


def test_the_poll_gets_the_callers_token_and_conversation():
    seen = {}

    async def poll(body, request, user):
        seen.update(body=body, token=request.state.token, user=user)
        return _complete()

    asyncio.run(
        runner.drain_genie_poll(IDS, obo_token="tok", email="me@example.com", role="User", poll_fn=poll)
    )
    assert seen["token"] == "tok"
    assert (seen["body"].conversation_id, seen["body"].message_id) == ("conv-1", "msg-1")
    assert seen["user"].email == "me@example.com"


def test_a_failed_genie_conversation_is_reported_with_its_reason():
    outcome, _ = _drain([GeniePollResponse(status="failed", error="Genie stopped before it could finish")])
    assert outcome == {"status": "failed", "error": "Genie stopped before it could finish"}


def test_an_auth_failure_from_the_poll_endpoint_is_a_failed_outcome():
    outcome, _ = _drain([HTTPException(status_code=401, detail="No Genie credentials")])
    assert outcome == {"status": "failed", "error": "No Genie credentials"}


def test_a_genie_that_never_settles_times_out_instead_of_hanging():
    outcome, sleeps = _drain([GeniePollResponse(status="running")] * 3, timeout=0)
    assert outcome["status"] == "timeout" and sleeps == []


# --------------------------------------------------------------- what is shown
def test_columns_may_be_names_or_objects_and_rows_are_counted():
    table = runner.genie_result_table(RESULT)
    assert table == {"columns": ["c_name", "total"], "rows": [["A", "9"], ["B", "8"]], "total": 2}
    assert runner.genie_result_table({"final_answer": "text only"}) is None
    assert runner.genie_result_table({"attachments": [{"query": {"result": {"columns": ["a"], "rows": []}}}]}) is None


def test_the_user_sees_genies_written_answer_as_is():
    assert runner.genie_answer({"status": "complete", "result": RESULT}) == "Customer A leads."
    assert runner.genie_answer({"status": "complete", "result": {"text": " from text "}}) == "from text"
    assert "no written answer" in runner.genie_answer({"status": "complete", "result": {}})
    failed = runner.genie_answer({"status": "failed", "error": "ran out of steps"})
    assert failed.startswith("Genie couldn't answer") and "ran out of steps" in failed


def test_the_replayed_tool_message_lets_the_model_chart_and_continue():
    text = runner.genie_tool_message("top customers?", {"status": "complete", "result": RESULT})
    assert text.startswith("top customers?")
    assert "[Result columns: c_name, total (2 rows)." in text
    assert "call render_chart with these exact column names" in text
    assert "[Genie conversation_id: conv-1." in text and "pass it as conversation_id to ask_your_data" in text
    # No rows and no conversation id: nothing to add, so nothing invented.
    assert runner.genie_tool_message("q", {"status": "complete", "result": {"final_answer": "x"}}) == "q"


def test_the_replay_wording_matches_what_the_chat_sends():
    """The notes are what teach the model to chart and to continue a conversation, so a
    reworded frontend would make this suite test something users no longer get."""
    chat = Path(__file__).resolve().parents[4] / "src" / "components" / "chat" / "ChatView.tsx"
    source = chat.read_text()
    for phrase in (
        "For a follow-up to this answer, pass it as conversation_id to ask_your_data.]",
        "To visualize, call render_chart with these exact column names.]",
        "[Genie conversation_id: ",
        "[Result columns: ",
    ):
        assert phrase in source, f"ChatView no longer sends {phrase!r}; update runner.genie_tool_message"


def test_the_judge_sees_what_genie_returned():
    evidence = runner.genie_evidence({"status": "complete", "result": RESULT})
    assert "conversation_id=conv-1" in evidence
    assert "SQL: SELECT c_name FROM customer" in evidence
    assert "Columns: c_name, total. 2 row(s)" in evidence and '["A", "9"]' in evidence
    assert "No result rows" in runner.genie_evidence({"status": "complete", "result": {"final_answer": "x"}})
    assert "failed" in runner.genie_evidence({"status": "failed", "error": "boom"})


def test_a_genie_turn_is_replayed_the_way_the_browser_does():
    pending = {"kind": "genie", "tool_call_id": "tc-1", "tool_name": "ask_your_data", "ids": IDS}
    records = runner.genie_turn_records(
        "top customers?",
        pending,
        {"status": "complete", "result": RESULT},
        "Customer A leads.",
        {"question": "top customers"},
    )
    user, call, tool, answer = records["history"]
    assert user == {"role": "user", "content": "top customers?"}
    # The tool message needs the assistant tool_call before it, or the model API rejects it.
    assert call["role"] == "assistant" and call["tool_calls"][0]["id"] == "tc-1"
    assert call["tool_calls"][0]["function"]["name"] == "ask_your_data"
    assert tool["role"] == "tool" and tool["tool_call_id"] == "tc-1" and "conv-1" in tool["content"]
    assert answer == {"role": "assistant", "content": "Customer A leads."}
    assert [m["role"] for m in records["transcript"]] == ["tool", "assistant"]


# ------------------------------------------------------- through the agent loop
class _FakeAgent:
    """Stands in for AgentRunner: turn 1 hands off to Genie, later turns answer plainly."""

    histories = []

    def __init__(self, **_kw):
        pass

    async def run(self, query, history=None, obo_token=None):
        type(self).histories.append(list(history or []))
        if len(type(self).histories) == 1:
            args = {"question": "top customers?"}
            return {
                "content": "",
                "tool_calls": [
                    {"id": "tc-1", "type": "function", "function": {"name": "ask_your_data", "arguments": args}}
                ],
                "messages": [],
                "pending_poll": {
                    "kind": "genie",
                    "ids": IDS,
                    "friendly_label": "Asking Genie...",
                    "tool_call_id": "tc-1",
                    "tool_name": "ask_your_data",
                },
            }
        return {"content": "Done.", "tool_calls": [], "messages": [], "pending_poll": None}


def test_a_turn_ending_in_a_genie_poll_is_settled_and_replayed_on_the_next_turn(monkeypatch):
    import app.agents.runner as agent_runner

    _FakeAgent.histories = []
    monkeypatch.setattr(agent_runner, "AgentRunner", _FakeAgent)

    async def settled(ids, **_kw):
        assert ids == IDS
        return {"status": "complete", "result": RESULT}

    monkeypatch.setattr(runner, "drain_genie_poll", settled)

    case = _case(turns=["Ask Genie: top customers?", "Now the top three."])
    out = asyncio.run(runner.run_agent(case, tools=[], email="me@example.com", obo_token="tok"))

    # The tool call that started Genie is recorded with its arguments, for the checks.
    assert [(c["name"], c["turn"], c["arguments"]) for c in out["tool_calls"]] == [
        ("ask_your_data", 1, {"question": "top customers?"})
    ]
    # Turn 2 saw what the browser would replay, including the conversation id.
    replayed = _FakeAgent.histories[1]
    assert [m["role"] for m in replayed] == ["user", "assistant", "tool", "assistant"]
    assert "[Genie conversation_id: conv-1." in replayed[2]["content"]
    assert replayed[3]["content"] == "Customer A leads."
    # The judge reads what Genie returned, then what the user was shown.
    roles = [(m["role"], m.get("name")) for m in out["transcript"]]
    assert roles == [
        ("user", None), ("tool", "ask_your_data"), ("assistant", None),
        ("user", None), ("assistant", None),
    ]
    assert out["answer"] == "Done."


def test_a_failed_genie_turn_shows_the_user_that_it_failed(monkeypatch):
    import app.agents.runner as agent_runner

    _FakeAgent.histories = []
    monkeypatch.setattr(agent_runner, "AgentRunner", _FakeAgent)

    async def failed(ids, **_kw):
        return {"status": "failed", "error": "Genie stopped before it could finish."}

    monkeypatch.setattr(runner, "drain_genie_poll", failed)
    out = asyncio.run(runner.run_agent(_case(), tools=[], email="me@example.com", obo_token="tok"))
    assert out["answer"].startswith("Genie couldn't answer this.")
    assert "stopped before it could finish" in out["answer"]
