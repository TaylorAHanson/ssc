"""
LLM-authored runtime playbooks for a workflow, with a deterministic fallback.

``instructions.py`` derives a *baseline* playbook from the graph — accurate but
thin: it can only describe the vars the steps happen to reference. This module
asks the LLM to author the real thing (what to gather and validate, how to push
back, what the approvals mean, where it can go wrong) from the graph plus the
admin's goal, so the studio has an explicit "generate/improve instructions"
action instead of the author staring at an empty textarea.

Never raises: if the model is unavailable or returns something unusable, the
caller gets the deterministic baseline. Blank instructions are the failure mode
this whole module exists to prevent, so it must not be able to cause one.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.workflows.instructions import (
    render_instructions_markdown,
    with_canonical_execution,
)

logger = logging.getLogger(__name__)

_MAX_TOKENS = 2000

_AUTHOR_PROMPT = """You are authoring the runtime playbook for a governed \
self-service workflow. A conversational agent reads this markdown to decide what \
to gather from the requester, what to validate, and what to tell them about \
approvals. It is the agent's instructions — write it for the agent, not as \
documentation about the workflow.

WORKFLOW
- Key / request type: {request_type}
- Name: {name}
- Goal (from the admin): {goal}

GRAPH (what actually runs, in order)
{graph_summary}

INPUTS THE STEPS CONSUME (these MUST each be gathered)
{inputs}

{catalog_context}
Write markdown with these sections, in this order:

# <Title> Instructions
**Goal**: <one line>

## Information to Gather
One numbered entry per input above, using its exact `var_name` in backticks. For
each: what it means in this organization, whether it is required, the expected
format or example, and how the agent should validate or disambiguate it. If an
input needs a naming convention or an existence check, say so.

## Validation & Pushback
What the agent must check before submitting, and when it should challenge the
request instead of fulfilling it (over-broad scope, a cheaper existing
alternative, missing justification). Be specific to this workflow.

## Flow & Approvals
Plain-language walk-through of the stages above, including what each approval
gate means for the requester and roughly how long they wait.

## Assumptions
Bullets: anything you had to assume because the graph or goal didn't say. Be
honest and specific — the admin will correct these.

## Open Questions & Risks
Bullets: the design questions the admin still needs to answer (who approves,
who owns the resource, does access expire, which cost center, what happens on
rejection) and the risks of the current design.

Rules:
- Do NOT invent tools, gates, or steps that are not in the graph above.
- Do NOT include an `## Execution` section — the call contract is generated
  from the graph and appended automatically.
- Be concrete and imperative. No preamble, no closing commentary. Output only
  the markdown.
"""


def _graph_summary(spec: Dict[str, Any]) -> str:
    from app.workflows.instructions import _GATE_DESCRIPTIONS, _humanize

    stages = (spec or {}).get("stages") or []
    if not stages:
        return "(no stages defined yet — the graph is still empty)"
    lines = []
    for i, stage in enumerate(stages, 1):
        if not isinstance(stage, dict):
            continue
        kind = stage.get("kind")
        name = _humanize(str(stage.get("name") or f"stage {i}"))
        cond = " [conditional]" if stage.get("run_if") else ""
        if kind == "gate":
            gtype = stage.get("type") or "manager"
            desc = _GATE_DESCRIPTIONS.get(gtype, f"{_humanize(gtype)} approval")
            extra = ""
            if gtype == "manual_task" and stage.get("instructions"):
                extra = f" — task: {stage['instructions']}"
            auto = " (auto-approves when its condition holds)" if stage.get("auto_approve") else ""
            lines.append(f"{i}. GATE {name}: {desc}{auto}{extra}{cond}")
        elif kind == "subworkflow":
            lines.append(f"{i}. CALL WORKFLOW {name}: runs `{stage.get('ref')}`{cond}")
        else:
            lines.append(f"{i}. STEP {name}: calls tool `{stage.get('tool')}`{cond}")
    return "\n".join(lines)


def _inputs_summary(spec: Dict[str, Any]) -> str:
    from app.workflows.instructions import _user_inputs

    inputs = _user_inputs(spec or {})
    if not inputs:
        return (
            "(none referenced by the steps — decide what the agent still needs to "
            "confirm before submitting)"
        )
    return "\n".join(f"- {v}" for v in inputs)


def _looks_like_a_playbook(md: str) -> bool:
    """Cheap sanity check before we let model output become the runtime prompt."""
    if len(md.strip()) < 200:
        return False
    lowered = md.lower()
    return "## information to gather" in lowered


async def generate_instructions(
    spec: Optional[Dict[str, Any]],
    *,
    request_type: Optional[str] = None,
    goal: Optional[str] = None,
    name: Optional[str] = None,
    catalog_context: str = "",
) -> Dict[str, Any]:
    """Author a runtime playbook for ``spec``.

    Returns ``{"instructions_markdown", "source", "warning"}`` where ``source`` is
    ``"llm"`` or ``"auto_baseline"`` — the caller surfaces that so an author can
    tell a real playbook from the generated stub.
    """
    spec = spec or {}
    baseline = render_instructions_markdown(spec, request_type=request_type, goal=goal)

    from app.model_serving.agent_llm import AgentLLMClient

    try:
        client = AgentLLMClient()
    except ValueError:  # no endpoint configured at all
        return {
            "instructions_markdown": baseline,
            "source": "auto_baseline",
            "warning": (
                "No LLM endpoint is configured, so this is the graph-derived baseline. "
                "Edit it before publishing."
            ),
        }

    prompt = _AUTHOR_PROMPT.format(
        request_type=request_type or spec.get("name") or "(not set)",
        name=name or request_type or spec.get("name") or "(not set)",
        goal=goal or "(the admin has not stated one — infer it and list it as an assumption)",
        graph_summary=_graph_summary(spec),
        inputs=_inputs_summary(spec),
        catalog_context=(f"{catalog_context}\n" if catalog_context else ""),
    )

    try:
        authored = (
            await client.complete_text(prompt, temperature=0.2, max_tokens=_MAX_TOKENS)
        ).strip()
    except Exception as e:  # noqa: BLE001 - fall back rather than fail the request
        logger.warning("Instruction generation failed, using baseline: %s", e)
        authored = ""

    if not _looks_like_a_playbook(authored):
        return {
            "instructions_markdown": baseline,
            "source": "auto_baseline",
            "warning": (
                "Could not author a playbook this time, so this is the graph-derived "
                "baseline. Edit it before publishing."
            ),
        }

    # The execute_workflow contract is always graph-derived, never model-written.
    # With no graph there is nothing to derive, so we leave the prose alone rather
    # than appending a call contract with no parameters.
    if spec.get("stages"):
        authored = with_canonical_execution(authored, spec, request_type=request_type)

    return {"instructions_markdown": authored, "source": "llm", "warning": None}
