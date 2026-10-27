"""The assistant test library: case loading, deterministic checks, and run plumbing.

No model is called: the agent and the judge are replaced with fakes, so these tests
cover the part that must be right for a before/after comparison to mean anything.
"""
import asyncio

import pytest

from app.agents.eval import checks, library, runner
from app.agents.eval.library import EvalCase, LibraryError, parse_suite


def _suite(**case_overrides):
    case = {"id": "c1", "question": "Who owns ${table}?", "expected": "Names the owner."}
    case.update(case_overrides)
    return {"suite": "s", "vars": {"table": "a.b.c"}, "cases": [case]}


def _case(**overrides) -> EvalCase:
    base = dict(id="c1", suite="s", name="c1", turns=["hi"], expected="ok")
    base.update(overrides)
    return EvalCase(**base)


# ------------------------------------------------------------ the shipped library
def test_shipped_library_loads_with_unique_ids_and_resolved_variables():
    suites = library.load_suites()
    assert suites, "the library ships with at least one suite"
    ids = [c.id for s in suites for c in s.cases]
    assert len(ids) == len(set(ids))
    for s in suites:
        for c in s.cases:
            assert "${" not in " ".join(c.turns) + c.expected, f"{c.id}: unresolved variable"
            assert c.expected and c.turns


def test_shipped_cases_only_name_tools_that_exist():
    """A typo in expect_tools would silently turn a check into a permanent fail,
    and a typo in forbid_tools into a permanent pass."""
    from app.tools import catalog

    known = {name for name, _tool, _origin in catalog.all_tools()}
    for s in library.load_suites():
        for c in s.cases:
            named = list(c.forbid_tools) + list(c.tool_limits)
            for req in c.expect_tools:
                named.extend([req] if isinstance(req, str) else req)
            unknown = sorted(set(named) - known)
            assert not unknown, f"{c.id} names tools that do not exist: {unknown}"


# ----------------------------------------------------------------------- loading
def test_variables_are_substituted_and_overridable():
    suite = parse_suite(_suite(), overrides={"table": "x.y.z"})
    assert suite.cases[0].turns == ["Who owns x.y.z?"]
    assert parse_suite(_suite()).cases[0].turns == ["Who owns a.b.c?"]


def test_unknown_variable_is_an_error_not_silent_text():
    with pytest.raises(LibraryError, match="unknown variable"):
        parse_suite(_suite(question="Who owns ${typo}?"))


def test_question_shorthand_and_turns_are_equivalent():
    one = parse_suite(_suite()).cases[0]
    many = parse_suite(_suite(question=None, turns=["Who owns ${table}?"])).cases[0]
    assert one.turns == many.turns


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"expected": ""}, "needs an 'expected'"),
        ({"question": None}, "needs 'question'"),
        ({"answer_matches": ["("]}, "bad pattern"),
        ({"tool_limits": ["x"]}, "tool_limits"),
    ],
)
def test_malformed_cases_are_rejected_with_the_case_named(bad, message):
    with pytest.raises(LibraryError, match=message) as exc:
        parse_suite(_suite(**bad), source="f.yaml")
    assert "c1" in str(exc.value) or "f.yaml" in str(exc.value)


def test_duplicate_ids_are_rejected():
    raw = _suite()
    raw["cases"].append(dict(raw["cases"][0]))
    with pytest.raises(LibraryError, match="duplicate id"):
        parse_suite(raw)


def test_suite_defaults_apply_to_every_case_and_cases_can_override_the_role():
    raw = _suite()
    raw["defaults"] = {"role": "Data Steward", "answer_not_matches": ["secret"]}
    raw["cases"].append({"id": "c2", "question": "q", "expected": "e", "role": "User"})
    first, second = parse_suite(raw).cases
    assert first.role == "Data Steward" and second.role == "User"
    assert "secret" in first.answer_not_matches and "secret" in second.answer_not_matches


def test_select_filters_and_unknown_ids_are_an_error():
    raw = _suite(tags=["a"])
    raw["cases"].append({"id": "c2", "question": "q", "expected": "e", "enabled": False})
    suites = [parse_suite(raw)]
    assert [c.id for c in library.select_cases(suites)] == ["c1"]
    assert [c.id for c in library.select_cases(suites, tags=["a"])] == ["c1"]
    assert [c.id for c in library.select_cases(suites, include_disabled=True)] == ["c1", "c2"]
    with pytest.raises(LibraryError, match="no such case"):
        library.select_cases(suites, case_ids=["nope"])


# ------------------------------------------------------------------------ checks
def test_expect_tools_accepts_any_of_a_group():
    case = _case(expect_tools=[["find_owner", "get_metadata"], "submit_feedback"])
    res = checks.evaluate_checks(case, ["get_metadata", "submit_feedback"], "")
    assert all(r["ok"] for r in res)
    res = checks.evaluate_checks(case, ["get_metadata"], "")
    assert [r["ok"] for r in res] == [True, False]


def test_max_tool_calls_catches_a_looping_assistant():
    case = _case(max_tool_calls=2)
    assert all(r["ok"] for r in checks.evaluate_checks(case, ["a", "b"], ""))
    bad = checks.failed_checks(checks.evaluate_checks(case, ["a", "b", "a"], ""))
    assert len(bad) == 1 and "3 calls: a, b, a" in bad[0]["detail"]


def test_max_tool_calls_comes_from_suite_defaults_and_is_validated():
    raw = _suite()
    raw["defaults"] = {"max_tool_calls": 5}
    raw["cases"].append({"id": "c2", "question": "q", "expected": "e", "max_tool_calls": 2})
    first, second = parse_suite(raw).cases
    assert (first.max_tool_calls, second.max_tool_calls) == (5, 2)
    with pytest.raises(LibraryError, match="max_tool_calls"):
        parse_suite(_suite(max_tool_calls=0))
    with pytest.raises(LibraryError, match="max_tool_calls"):
        parse_suite(_suite(max_tool_calls="lots"))


def test_forbidden_tools_and_limits():
    case = _case(forbid_tools=["execute_workflow"], tool_limits={"submit_feedback": 1})
    assert all(r["ok"] for r in checks.evaluate_checks(case, ["submit_feedback"], ""))
    bad = checks.failed_checks(
        checks.evaluate_checks(case, ["execute_workflow", "submit_feedback", "submit_feedback"], "")
    )
    assert {r["check"] for r in bad} == {"does not call execute_workflow", "calls submit_feedback at most 1x"}


def test_answer_patterns_match_case_insensitively_and_quote_a_violation():
    case = _case(answer_matches=["owner"], answer_not_matches=["access token"])
    assert all(r["ok"] for r in checks.evaluate_checks(case, [], "The OWNER is Sam."))
    bad = checks.failed_checks(
        checks.evaluate_checks(case, [], "Your Access Token expired, but the owner is Sam.")
    )
    assert len(bad) == 1 and "Access Token" in bad[0]["detail"]


# --------------------------------------------------------------------------- run
def _agent(answer="done", tools=()):
    async def fake(case, **_kw):
        return {
            "answer": answer,
            "tool_calls": [{"name": t, "arguments": {}} for t in tools],
            "transcript": [{"role": "user", "content": case.turns[0]}],
        }

    return fake


def _judge(verdict="pass"):
    async def fake(case, **_kw):
        return {"verdict": verdict, "score": 90, "rationale": "r", "missing": []}

    return fake


def _run(case, **kw):
    kw.setdefault("tools", [])
    return asyncio.run(runner.run_case(case, **kw))


def test_passing_case_needs_checks_and_judge_to_agree():
    res = _run(_case(expect_tools=["find_owner"]), agent=_agent(tools=["find_owner"]), judge_fn=_judge())
    assert res["status"] == "pass" and res["judge"]["score"] == 90


def test_a_failed_check_fails_the_case_even_if_the_judge_passes():
    res = _run(_case(expect_tools=["find_owner"]), agent=_agent(tools=[]), judge_fn=_judge("pass"))
    assert res["status"] == "fail"
    assert runner.failed_checks(res["checks"])


def test_judge_verdict_is_used_when_checks_pass():
    assert _run(_case(), agent=_agent(), judge_fn=_judge("partial"))["status"] == "partial"
    assert _run(_case(), agent=_agent(), judge_fn=_judge("fail"))["status"] == "fail"


def test_no_judge_grades_on_checks_alone_and_never_calls_the_judge():
    async def boom(*_a, **_k):
        raise AssertionError("judge must not run")

    assert _run(_case(), agent=_agent(), judge_fn=boom, use_judge=False)["status"] == "pass"


def test_identity_cases_are_skipped_not_passed_without_a_token():
    async def boom(*_a, **_k):
        raise AssertionError("agent must not run")

    res = _run(_case(needs_identity=True), agent=boom, judge_fn=_judge())
    assert res["status"] == "skipped" and "identity" in res["error"]
    assert _run(_case(needs_identity=True), agent=_agent(), judge_fn=_judge(), obo_token="t")["status"] == "pass"


def test_agent_failure_and_timeout_are_errors_that_do_not_end_the_run():
    async def broken(*_a, **_k):
        raise RuntimeError("model down")

    async def slow(*_a, **_k):
        await asyncio.sleep(5)

    assert "model down" in _run(_case(), agent=broken)["error"]
    res = _run(_case(), agent=slow, timeout=0.05)
    assert res["status"] == "error" and "did not finish" in res["error"]


MODEL_DOWN = "I encountered an error connecting to the model. Please try again."


def test_a_model_outage_is_retried_then_reported_as_an_error_not_graded():
    calls = {"n": 0}

    async def always_down(case, **_kw):
        calls["n"] += 1
        return {"answer": MODEL_DOWN, "tool_calls": [], "transcript": []}

    async def boom(*_a, **_k):
        raise AssertionError("an outage must not reach the judge")

    res = _run(_case(), agent=always_down, judge_fn=boom, retries=2, retry_backoff=0)
    assert res["status"] == "error" and res["attempts"] == 3 and calls["n"] == 3
    assert "not a verdict" in res["error"]


def test_a_model_outage_that_clears_is_graded_normally():
    calls = {"n": 0}

    async def flaky(case, **_kw):
        calls["n"] += 1
        answer = MODEL_DOWN if calls["n"] == 1 else "The owner is Sam."
        return {"answer": answer, "tool_calls": [], "transcript": []}

    res = _run(_case(), agent=flaky, judge_fn=_judge(), retries=2, retry_backoff=0)
    assert res["status"] == "pass" and res["attempts"] == 2


def test_judge_failure_keeps_the_evidence_and_is_an_error():
    async def bad_judge(*_a, **_k):
        raise ValueError("no json")

    res = _run(_case(), agent=_agent(answer="hello"), judge_fn=bad_judge)
    assert res["status"] == "error" and res["answer"] == "hello"


def test_run_cases_resolves_tools_once_per_role_and_keeps_library_order():
    seen = []
    cases = [_case(id="a", role="User"), _case(id="b", role="Admin"), _case(id="c", role="User")]

    def resolver(role):
        seen.append(role)
        return []

    results = asyncio.run(
        runner.run_cases(cases, tools_for_role=resolver, agent=_agent(), judge_fn=_judge(), concurrency=2)
    )
    assert [r["id"] for r in results] == ["a", "b", "c"]
    assert sorted(seen) == ["Admin", "User"]


def test_multi_turn_transcript_keeps_only_the_current_turn():
    summary = [
        {"role": "user", "content": "turn one"},
        {"role": "assistant", "content": "answer one"},
        {"role": "user", "content": "turn two (wrapped)"},
        {"role": "assistant", "content": "calling", "tool_calls": [{"name": "t"}]},
        {"role": "tool", "content": "{}"},
        {"role": "assistant", "content": "answer two"},
    ]
    kept = runner._after_last_user_message(summary)
    assert [m["content"] for m in kept] == ["calling", "{}", "answer two"]


# ------------------------------------------------------------------------ report
def _report(statuses, standing=1000):
    """``statuses`` maps a case id to one status, or to a list (one per repeated run)."""
    results = []
    for case_id, status in statuses.items():
        for run, st in enumerate([status] if isinstance(status, str) else status, 1):
            results.append({"id": case_id, "status": st, "name": case_id, "checks": [], "run": run})
    return {
        "label": "x",
        "created_at": "t",
        "summary": runner.summarize(results),
        "results": results,
        "footprint": {"standing_tokens": standing, "tool_count": 5},
    }


def test_summary_pass_rate_only_counts_cases_that_were_graded():
    s = runner.summarize(
        [{"status": "pass"}, {"status": "fail"}, {"status": "skipped"}, {"status": "error"}]
    )
    assert s["graded"] == 2 and s["pass_rate"] == 0.5
    assert s["skipped"] == 1 and s["error"] == 1


def test_compare_calls_out_regressions_improvements_and_footprint():
    before = _report({"a": "pass", "b": "fail", "c": "pass"}, standing=37000)
    after = _report({"a": "fail", "b": "pass", "c": "pass"}, standing=25000)
    text = runner.compare_reports(before, after)
    assert "REGRESSIONS" in text and "a: passed 1/1 -> 0/1" in text
    assert "IMPROVEMENTS" in text and "b: passed 0/1 -> 1/1" in text
    assert "~37000 -> ~25000" in text and "unchanged: 1" in text


def test_compare_ignores_one_flipped_run_among_repeats_but_not_a_real_drop():
    before = _report({"noisy": ["pass"] * 3, "broken": ["pass"] * 3})
    after = _report({"noisy": ["pass", "pass", "fail"], "broken": ["fail", "fail", "pass"]})
    text = runner.compare_reports(before, after)
    assert "broken: passed 3/3 -> 1/3" in text
    assert "noisy" not in text.split("REGRESSIONS:")[1].split("unchanged")[0].replace("broken", "")


def test_compare_leaves_out_cases_that_were_never_graded():
    before = _report({"a": "skipped", "b": "pass"})
    after = _report({"a": "pass", "b": "error"})
    text = runner.compare_reports(before, after)
    assert "REGRESSIONS" not in text and "IMPROVEMENTS" not in text and "unchanged: 0" in text


def test_repeat_runs_every_case_that_many_times_in_order():
    cases = [_case(id="a"), _case(id="b")]
    results = asyncio.run(
        runner.run_cases(
            cases, tools_for_role=lambda role: [], agent=_agent(), judge_fn=_judge(), repeat=3
        )
    )
    assert [(r["id"], r["run"]) for r in results] == [
        ("a", 1), ("a", 2), ("a", 3), ("b", 1), ("b", 2), ("b", 3),
    ]


# ------------------------------------------------------------ preconditions and identity
def test_requires_workflows_is_parsed_and_defaults_to_nothing():
    plain, gated = parse_suite(
        {
            "suite": "s",
            "cases": [
                {"id": "a", "question": "q", "expected": "e"},
                {"id": "b", "question": "q", "expected": "e", "requires_workflows": ["end_user_access_request"]},
            ],
        }
    ).cases
    assert plain.requires_workflows == [] and gated.requires_workflows == ["end_user_access_request"]


def test_a_case_whose_workflow_is_not_published_is_skipped_not_failed():
    async def boom(*_a, **_k):
        raise AssertionError("agent must not run")

    case = _case(requires_workflows=["end_user_access_request"])
    res = _run(case, agent=boom, judge_fn=_judge(), published_workflows={"rbac_access_change"})
    assert res["status"] == "skipped"
    assert "end_user_access_request" in res["error"]
    # Present: it runs. Not checked at all (None): it runs too, as library users expect.
    assert _run(case, agent=_agent(), judge_fn=_judge(), published_workflows={"end_user_access_request"})[
        "status"
    ] == "pass"
    assert _run(case, agent=_agent(), judge_fn=_judge())["status"] == "pass"


def test_run_cases_only_looks_up_workflows_when_a_case_needs_them(monkeypatch):
    looked = []
    monkeypatch.setattr(runner, "published_workflow_keys", lambda: looked.append(1) or {"a_workflow"})

    def run(cases):
        return asyncio.run(
            runner.run_cases(
                cases, tools_for_role=lambda _r: [], agent=_agent(), judge_fn=_judge(), concurrency=1
            )
        )

    assert run([_case(id="x")])[0]["status"] == "pass" and looked == []
    results = run([_case(id="y", requires_workflows=["missing"]), _case(id="z", requires_workflows=["a_workflow"])])
    assert looked == [1]
    assert [r["status"] for r in results] == ["skipped", "pass"]


def test_the_callers_groups_reach_the_agent_and_the_identity(monkeypatch):
    """With a token the assistant sees the same groups the app resolves at login."""
    monkeypatch.setattr(runner, "resolve_entitlements", lambda email, token: [email, "analysts", "data-stewards"])
    seen = {}

    async def agent(case, **kw):
        seen.update(kw)
        return {"answer": "ok", "tool_calls": [], "transcript": []}

    asyncio.run(
        runner.run_cases(
            [_case()], tools_for_role=lambda _r: [], agent=agent, judge_fn=_judge(),
            obo_token="tok", email="me@example.com", with_user_context=False,
        )
    )
    assert seen["entitlements"] == ["me@example.com", "analysts", "data-stewards"]
    identity = runner._identity("me@example.com", "User", seen["entitlements"])
    assert identity["entitlements"] == "me@example.com, analysts, data-stewards"
    assert runner.synthetic_user("me@example.com", "User", seen["entitlements"]).entitlements[1] == "analysts"


def test_without_a_token_the_synthetic_user_has_no_groups(monkeypatch):
    original = runner.resolve_entitlements

    def must_not_resolve(*_a):
        raise AssertionError("no token, nothing to look up")

    monkeypatch.setattr(runner, "resolve_entitlements", must_not_resolve)
    seen = {}

    async def agent(case, **kw):
        seen.update(kw)
        return {"answer": "ok", "tool_calls": [], "transcript": []}

    asyncio.run(runner.run_cases([_case()], tools_for_role=lambda _r: [], agent=agent, judge_fn=_judge()))
    assert seen["entitlements"] == []
    assert original("me@example.com", None) == []
