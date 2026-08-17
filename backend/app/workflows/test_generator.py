"""
Propose test cases for a workflow (LLM), with a deterministic fallback.

An author staring at an empty Tests tab writes no tests, so both the studio's
"Propose cases" button and the assistant's ``save_workflow_tests`` tool come here
for a starting set. The cases are proposals: they land in the tab as editable rows,
they are never run automatically, and the author owns them.

Coverage is opinionated on purpose — the five shapes below are the ones that catch
real breakage: the happy path, a missing required input (does it ask instead of
guess?), something out of scope (does it refuse?), an ambiguous ask (does it
disambiguate?), and the rejection path.

Never raises: a failure returns the deterministic set rather than nothing.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_TOKENS = 1600
_MAX_CASES = 6

_PROMPT = """You are writing test cases for one workflow of a governed \
self-service data platform agent. Each case is a question a user might send, plus \
a plain-English description of what SHOULD happen. An LLM judge will later compare \
the agent's real behavior against your expected outcome, so describe observable \
behavior — what it asks for, what it refuses, which tool it calls — not exact wording.

WORKFLOW
- Key / request type: {request_type}
- Name: {name}
- Goal: {goal}

GRAPH (what actually runs, in order)
{graph_summary}

INPUTS THE STEPS CONSUME (each must be gathered from the user)
{inputs}

RUNTIME INSTRUCTIONS THE AGENT WILL FOLLOW
{instructions}

Write {count} cases covering, in this order:
1. happy path — everything supplied, the request goes through
2. missing required field — one required input omitted; the agent must ASK, not guess
3. out of scope — something this workflow must refuse or redirect
4. ambiguous input — the agent must disambiguate before acting
5. rejection / edge path — an approval gate says no, or a validation rule blocks it

Rules:
- Questions must sound like a real user, using realistic values for this workflow.
- An expected outcome must be checkable from a transcript. "Handles it correctly" \
is useless; "asks for the catalog name and does not call any provisioning tool" is good.
- Never expect the agent to invent a value the user did not give.
- Do not reference tools or gates that are not in the graph above.

Return ONLY a compact JSON array, no prose and no code fence:
[{{"name": "<short label>", "question": "<what the user says>", \
"expected_outcome": "<what should happen>"}}]
"""


def _fallback_cases(
    *, name: Optional[str], request_type: Optional[str], inputs: List[str]
) -> List[Dict[str, str]]:
    """Deterministic starter cases when no model is available.

    Written as prompts to the author rather than pretend-real tests: a placeholder
    that *looks* like a test would get run and produce a meaningless verdict.
    """
    label = name or request_type or "this workflow"
    first_input = inputs[0] if inputs else "the required details"
    return [
        {
            "name": "Happy path",
            "question": f"I need to {label.lower()} — replace this with a realistic ask, "
                        f"including {first_input}.",
            "expected_outcome": (
                "The agent gathers every required input, confirms the details back to "
                "the user, and submits the request. Fill in what 'correct' means here "
                "before running this case."
            ),
        },
        {
            "name": "Missing required field",
            "question": f"Set up {label.lower()} for me. (Deliberately leave out "
                        f"{first_input}.)",
            "expected_outcome": (
                f"The agent asks for {first_input} instead of guessing or submitting, "
                "and calls no provisioning tool until it has it."
            ),
        },
        {
            "name": "Out of scope",
            "question": "Ask for something this workflow must refuse or hand off.",
            "expected_outcome": (
                "The agent declines to handle it under this workflow, explains why, and "
                "points the user to the right path. It does not improvise a workaround."
            ),
        },
    ]


async def generate_test_cases(
    spec: Optional[Dict[str, Any]],
    *,
    request_type: Optional[str] = None,
    name: Optional[str] = None,
    goal: Optional[str] = None,
    instructions_markdown: Optional[str] = None,
    count: int = 5,
) -> Dict[str, Any]:
    """Propose up to ``count`` cases for ``spec``.

    Returns ``{"cases": [...], "source": "llm" | "fallback", "warning": str|None}``.
    """
    from app.workflows.instructions import _user_inputs
    from app.workflows.instructions_generator import _graph_summary, _inputs_summary

    spec = spec or {}
    count = max(1, min(_MAX_CASES, count))
    inputs = _user_inputs(spec)

    from app.model_serving.agent_llm import AgentLLMClient

    try:
        client = AgentLLMClient()
    except ValueError:  # no endpoint configured at all
        return {
            "cases": _fallback_cases(name=name, request_type=request_type, inputs=inputs),
            "source": "fallback",
            "warning": (
                "No LLM endpoint is configured, so these are placeholders — edit each "
                "question and expected outcome before running them."
            ),
        }

    instructions = (instructions_markdown or "").strip()
    prompt = _PROMPT.format(
        request_type=request_type or spec.get("name") or "(not set)",
        name=name or request_type or "(not set)",
        goal=goal or "(the admin has not stated one)",
        graph_summary=_graph_summary(spec),
        inputs=_inputs_summary(spec),
        instructions=(instructions[:6000] if instructions else "(none authored yet)"),
        count=count,
    )

    try:
        raw = await client.complete_text(prompt, temperature=0.3, max_tokens=_MAX_TOKENS)
        cases = _parse_cases(raw)
    except Exception as e:  # noqa: BLE001 - propose something rather than fail
        logger.warning("Test-case generation failed, using fallback: %s", e)
        cases = []

    if not cases:
        return {
            "cases": _fallback_cases(name=name, request_type=request_type, inputs=inputs),
            "source": "fallback",
            "warning": (
                "Could not propose cases this time, so these are placeholders — edit "
                "them before running."
            ),
        }
    return {"cases": cases[:count], "source": "llm", "warning": None}


def _parse_cases(content: str) -> List[Dict[str, str]]:
    """Parse the model's JSON array, tolerating a fence or surrounding prose."""
    raw = (content or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not parse proposed test cases: %s", e)
        return []
    if not isinstance(data, list):
        return []

    out: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        expected = str(item.get("expected_outcome") or "").strip()
        if not question or not expected:
            continue
        out.append({
            "name": str(item.get("name") or "").strip() or "Untitled case",
            "question": question,
            "expected_outcome": expected,
        })
    return out
