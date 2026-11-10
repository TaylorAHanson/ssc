"""Eval library checks on what a tool call passed, and on tools a role lacks.

Also pins that a turn's tool results are replayed into the next turn the way the
chat does. No model is called.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.agents.eval import checks, runner
from app.agents.eval.library import EvalCase, LibraryError, ToolArgExpectation, parse_suite


def _case(**overrides) -> EvalCase:
    base = dict(id="g", suite="s", name="g", turns=["who owns it?"], expected="ok")
    base.update(overrides)
    return EvalCase(**base)


# ----------------------------------------------------------------- the library
def _suite(**case):
    base = {"id": "c", "question": "q", "expected": "e"}
    base.update(case)
    return {"suite": "s", "vars": {"v": "Customer"}, "cases": [base]}


def test_a_turns_tool_results_are_replayed_into_the_next_turn_as_the_chat_does():
    from app.agents.runner import _REUSED_PREFIX

    def call(call_id, name):
        return {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}

    messages = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "", "tool_calls": [call("old", "find_owner")]},
        {"role": "tool", "tool_call_id": "old", "name": "find_owner", "content": "{}"},
        {"role": "user", "content": "Which workspaces?"},
        {"role": "assistant", "content": "", "tool_calls": [call("a", "get_target_workspaces"), call("b", "run_sql")]},
        {"role": "tool", "tool_call_id": "a", "name": "get_target_workspaces", "content": '{"workspaces": []}'},
        {"role": "tool", "tool_call_id": "b", "name": "run_sql", "content": "x" * 5000},
        {"role": "assistant", "content": "", "tool_calls": [call("c", "get_target_workspaces")]},
        {"role": "tool", "tool_call_id": "c", "name": "get_target_workspaces", "content": _REUSED_PREFIX + "{}"},
        {"role": "assistant", "content": "One workspace."},
    ]
    replayed = runner.replayed_tool_messages(messages)
    assert [(m["role"], m.get("tool_call_id") or m["tool_calls"][0]["id"]) for m in replayed] == [
        ("assistant", "a"), ("tool", "a"), ("assistant", "b"), ("tool", "b")]
    assert replayed[1]["content"] == '{"workspaces": []}' and replayed[1]["name"] == "get_target_workspaces"
    assert replayed[3]["content"].endswith(" …[truncated]") and len(replayed[3]["content"]) < 4100


def test_requires_tools_and_tool_args_are_parsed():
    plain = parse_suite(_suite()).cases[0]
    assert plain.requires_tools == [] and plain.expect_tool_args == []

    case = parse_suite(
        _suite(
            requires_tools=["run_sql"],
            expect_tool_args=[
                {"tool": "find_owner", "arg": "object_name", "turn": 2, "matches": "\\S"},
                {"tool": "run_sql", "arg": "sql", "not_matches": "(?i)group by", "matches": "${v}"},
            ],
        )
    ).cases[0]
    assert case.requires_tools == ["run_sql"]
    assert case.expect_tool_args[0] == ToolArgExpectation(
        tool="find_owner", arg="object_name", matches="\\S", turn=2
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


# ------------------------------------------------------------------ the checks
def _calls(*calls):
    return [{"name": n, "arguments": a, "turn": t} for n, a, t in calls]


def test_a_turn_scoped_expectation_checks_that_turns_call():
    case = _case(expect_tool_args=[ToolArgExpectation("find_owner", "object_name", matches="\\S", turn=2)])
    ok = _calls(("find_owner", {"object_name": "a"}, 1), ("find_owner", {"object_name": "b"}, 2))
    lost = _calls(("find_owner", {"object_name": "a"}, 1), ("find_owner", {}, 2))

    assert all(c["ok"] for c in checks.evaluate_checks(case, [c["name"] for c in ok], "", ok))
    failed = checks.failed_checks(checks.evaluate_checks(case, [c["name"] for c in lost], "", lost))
    assert len(failed) == 1 and "turn 2" in failed[0]["check"] and "<not passed>" in failed[0]["detail"]


def test_an_argument_matching_a_forbidden_pattern_fails_the_check():
    case = _case(expect_tool_args=[ToolArgExpectation("run_sql", "sql", not_matches="(?i)\\bgroup by\\b")])
    grouped = _calls(("run_sql", {"sql": "SELECT a, count(*) FROM t GROUP BY a"}, 1))
    probe = _calls(("run_sql", {"sql": "SELECT 1 FROM t LIMIT 1"}, 1))
    assert checks.failed_checks(checks.evaluate_checks(case, ["run_sql"], "", grouped))
    assert not checks.failed_checks(checks.evaluate_checks(case, ["run_sql"], "", probe))


def test_every_matching_call_must_satisfy_the_expectation():
    case = _case(expect_tool_args=[ToolArgExpectation("run_sql", "sql", not_matches="(?i)group by")])
    mixed = _calls(("run_sql", {"sql": "select 1"}, 1), ("run_sql", {"sql": "select a group by a"}, 2))
    failed = checks.failed_checks(checks.evaluate_checks(case, ["run_sql"] * 2, "", mixed))
    assert len(failed) == 1 and "1 of 2" in failed[0]["detail"]


def test_an_expectation_on_a_call_that_never_happened_fails():
    case = _case(expect_tool_args=[ToolArgExpectation("run_sql", "sql", matches="x", turn=2)])
    only_first = _calls(("run_sql", {"sql": "x"}, 1))
    failed = checks.failed_checks(checks.evaluate_checks(case, ["run_sql"], "", only_first))
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

    case = _case(requires_tools=["find_access"])
    res = asyncio.run(runner.run_case(case, tools=[SimpleNamespace(name="run_sql")], agent=boom))
    assert res["status"] == "skipped" and "find_access" in res["error"]

    async def agent(case, **_kw):
        return {"answer": "ok", "tool_calls": [], "transcript": []}

    async def judge(case, **_kw):
        return {"verdict": "pass", "score": 90, "rationale": "r", "missing": []}

    res = asyncio.run(
        runner.run_case(
            case, tools=[SimpleNamespace(name="find_access")], agent=agent, judge_fn=judge
        )
    )
    assert res["status"] == "pass"
