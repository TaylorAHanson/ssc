"""Tests for the sandboxed workflow test runner and its LLM judge.

The runner is the thing that lets an admin click "Run" and have the *real* agent
answer a question, so the properties that matter are: the judge's output is parsed
defensively (an LLM will eventually return a fence, prose, or a bad verdict), a
case gets the tools a real turn would have, and nothing about a run can write.
"""
import pytest

from app.workflows import test_runner


# --- judge parsing --------------------------------------------------------

def test_parse_verdict_accepts_clean_json():
    out = test_runner._parse_verdict(
        '{"verdict": "pass", "score": 92, "rationale": "Asked for both fields.", '
        '"missing": []}'
    )
    assert out["verdict"] == "pass"
    assert out["score"] == 92
    assert out["missing"] == []


def test_parse_verdict_tolerates_fences_and_prose():
    """Told not to, models still wrap JSON. A run must not error over formatting."""
    out = test_runner._parse_verdict(
        'Here is my assessment:\n```json\n{"verdict": "fail", "score": 10, '
        '"rationale": "It provisioned without asking.", '
        '"missing": ["did not ask for justification"]}\n```\nHope that helps.'
    )
    assert out["verdict"] == "fail"
    assert out["missing"] == ["did not ask for justification"]


@pytest.mark.parametrize("verdict", ["excellent", "", "PASSED", None])
def test_unknown_verdict_becomes_partial(verdict):
    """An unrecognized verdict must not silently count as a pass — 'partial' makes
    the score threshold decide instead."""
    payload = {"verdict": verdict, "score": 80, "rationale": "x"}
    import json

    out = test_runner._parse_verdict(json.dumps(payload))
    assert out["verdict"] == "partial"


def test_non_numeric_score_becomes_none_not_zero():
    out = test_runner._parse_verdict('{"verdict": "pass", "score": "high", "rationale": "x"}')
    assert out["score"] is None


def test_score_is_clamped_to_0_100():
    assert test_runner._parse_verdict('{"verdict":"pass","score":140}')["score"] == 100
    assert test_runner._parse_verdict('{"verdict":"fail","score":-5}')["score"] == 0


def test_parse_verdict_raises_on_no_json():
    with pytest.raises(ValueError, match="did not return JSON"):
        test_runner._parse_verdict("I'm not sure how to grade this.")


@pytest.mark.asyncio
async def test_judge_case_sends_the_expectation_and_returns_a_verdict(monkeypatch):
    """The judge must be given the question, the expectation, and the tool calls —
    grading the transcript alone would just reward a confident-sounding answer."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AI_GATEWAY_ENDPOINT", "judge-endpoint", raising=False)

    captured = {}

    class _StubTransport:
        async def invoke_endpoint(self, endpoint, payload, endpoint_url=None, **kwargs):
            captured["endpoint"] = endpoint
            captured["endpoint_url"] = endpoint_url
            captured["model"] = payload.get("model")
            captured["prompt"] = payload["messages"][0]["content"]
            captured["temperature"] = payload.get("temperature")
            return {"choices": [{"message": {"content": '{"verdict":"pass","score":88,'
                                                        '"rationale":"Asked first.","missing":[]}'}}]}

    monkeypatch.setattr(
        "app.model_serving.agent_llm.get_model_serving_client", lambda: _StubTransport()
    )

    out = await test_runner.judge_case(
        question="I need access to the sales catalog",
        expected="Asks for a business justification before submitting anything",
        answer="What will you use the sales data for?",
        tool_calls=[{"name": "get_workflow_instructions", "arguments": {"key": "data_access"}}],
        transcript=[{"role": "user", "content": "I need access to the sales catalog"}],
    )

    assert out["verdict"] == "pass" and out["score"] == 88
    assert captured["endpoint"] == "judge-endpoint"
    assert "business justification" in captured["prompt"]
    assert "get_workflow_instructions" in captured["prompt"]
    # A rerun that flips verdicts for no reason destroys trust in the tab.
    assert captured["temperature"] == 0.0
    # The judge goes through AgentLLMClient, so a gateway model is named in the
    # body and posted to the gateway route. Resolving it by hand and POSTing to
    # /serving-endpoints/{model}/invocations is a 404 the chat agent never sees.
    assert captured["endpoint_url"] == "/ai-gateway/mlflow/v1/chat/completions"
    assert captured["model"] == "judge-endpoint"


@pytest.mark.asyncio
async def test_judge_case_requires_a_configured_endpoint(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "AI_GATEWAY_ENDPOINT", "", raising=False)
    monkeypatch.setattr(settings, "MODEL_SERVING_AGENT_LLM_ENDPOINT", "", raising=False)

    with pytest.raises(RuntimeError, match="cannot be judged"):
        await test_runner.judge_case(
            question="q", expected="e", answer="a", tool_calls=[], transcript=[],
        )


# --- tool scoping ---------------------------------------------------------

def test_test_tools_include_graph_steps_and_the_submit_tool():
    """`allowed_tools` is usually unset on graph-driven workflows, and without
    `execute_workflow` the agent can gather everything and still never finish — so
    every happy-path case would fail for a reason unrelated to the workflow."""
    spec = {
        "name": "wf",
        "stages": [
            {"kind": "gate", "name": "g", "type": "manager"},
            {"kind": "step", "name": "notify", "tool": "send_notification", "args": {}},
        ],
    }
    names = [t.name for t in test_runner._resolve_test_tools([], spec)]
    assert "send_notification" in names
    assert "execute_workflow" in names
    assert "get_workflow_instructions" in names


def test_unknown_allowed_tool_is_skipped_not_fatal():
    names = [t.name for t in test_runner._resolve_test_tools(["not_a_real_tool"], None)]
    assert "not_a_real_tool" not in names
    assert "execute_workflow" in names


# --- sandbox + identity ---------------------------------------------------

@pytest.mark.asyncio
async def test_run_agent_sandboxes_the_agent_and_uses_a_synthetic_identity(monkeypatch):
    """A test must never resolve to the launching admin's manager/groups, and must
    never be able to write — otherwise "run the tests" is a provisioning action."""
    built = {}

    class _StubRunner:
        def __init__(self, **kwargs):
            built.update(kwargs)
            self.system_prompt = "BASE PROMPT"

        async def run(self, query):
            built["query"] = query
            built["prompt_at_run"] = self.system_prompt
            return {
                "content": "What is this for?",
                "tool_calls": [{"id": "1", "function": {"name": "get_workflow_instructions",
                                                        "arguments": {"key": "wf"}}}],
                "messages": [
                    {"role": "system", "content": "should be dropped"},
                    {"role": "user", "content": "I need access"},
                    {"role": "assistant", "content": "What is this for?"},
                ],
            }

    monkeypatch.setattr("app.agents.runner.AgentRunner", _StubRunner)

    out = await test_runner._run_agent(
        {
            "key": "data_access", "name": "Data Access", "goal": "grant read access",
            "instructions_markdown": "## Information to Gather\n1. Catalog",
            "allowed_tools": [], "graph_spec": None,
        },
        "I need access",
    )

    assert built["dry_run"] is True
    assert built["user_identity"]["email"].endswith(".invalid")
    # The case tests THIS workflow, not the router that would have to find it.
    assert "WORKFLOW UNDER TEST" in built["prompt_at_run"]
    assert "Information to Gather" in built["prompt_at_run"]
    assert out["answer"] == "What is this for?"
    assert out["tool_calls"][0]["name"] == "get_workflow_instructions"
    # The system prompt is the workflow's own playbook; keeping it would bloat every
    # stored run with text already on screen.
    assert all(m["role"] != "system" for m in out["transcript"])
    # The answer is already the last history entry, so it isn't duplicated.
    assert [m["content"] for m in out["transcript"]].count("What is this for?") == 1


def test_transcript_keeps_the_final_answer_when_history_omits_it():
    """The runner returns the reply outside ``messages``; without appending it the
    stored transcript ends on the user's question and hides the main evidence."""
    transcript = test_runner._summarize_transcript(
        [{"role": "user", "content": "Can you also create a workspace?"}],
        answer="That's out of scope for this workflow.",
    )
    assert transcript[-1] == {
        "role": "assistant", "content": "That's out of scope for this workflow.",
    }
