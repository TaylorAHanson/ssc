"""
Sandboxed workflow test runner + LLM judge.

Runs a workflow's test cases the only way that actually proves anything: start the
**real** agent, with this workflow's own instructions markdown and its allowed
tools, and let it answer the case's question. Every mutating tool is simulated by
the ToolExecutor (``ToolContext.dry_run``), so a test can exercise a provisioning
workflow without provisioning anything.

An LLM judge then compares the transcript to the case's plain-English expected
outcome and returns ``{verdict, score, rationale, missing[]}``. That is stored on
the run row together with the transcript: the judge is non-deterministic, so a
verdict nobody can inspect is worthless, and every case must be re-runnable.

Threading: a run group executes on a dedicated daemon thread with its own event
loop. FastAPI ``BackgroundTasks`` run on the request-serving loop, and a workflow
of agent turns there freezes the whole app (the same reason the contract sync
thread exists).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """You are grading one test case for a governed self-service \
data platform's AI agent. The workflow author wrote a question and, in plain \
English, what should happen. Decide whether the agent's actual behavior matches.

Grade BEHAVIOR, not wording. The agent passes when it does the substance of what \
was expected — asks for the information the expectation says it should ask for, \
refuses what it should refuse, calls the tool it should call with sensible \
arguments, and does not invent facts or skip a required approval.

Important context for grading:
- Mutating tools ran in a SANDBOX. A tool result containing "dry_run": true means \
the call was correct and simply not executed — treat it as a successful call, not \
as a failure.
- Asking a clarifying question is CORRECT when the expectation says required \
information is missing. Do not penalize the agent for not guessing.
- Fabricating a value the user never supplied is a failure even if the final \
answer looks right.

Return ONLY compact JSON, no prose and no code fence:
{{"verdict": "pass" | "partial" | "fail", "score": <0-100>, \
"rationale": "<2-3 sentences citing what the agent actually did>", \
"missing": ["<expectation not met>", ...]}}

"score" is your confidence that the expected outcome was met (100 = fully met, \
0 = not at all). "missing" is empty on a clean pass.

QUESTION ASKED:
{question}

EXPECTED OUTCOME:
{expected}

WHAT THE AGENT ACTUALLY DID
Tools called (in order):
{tools}

Final answer to the user:
{answer}

Full transcript:
{transcript}
"""

# Caps keep a chatty run inside the judge's context window. The final answer gets
# the most room because it is what the expectation is usually about.
_MAX_ANSWER_CHARS = 6000
_MAX_TRANSCRIPT_CHARS = 12000
_MAX_TOOLS_CHARS = 4000


# --------------------------------------------------------------------------- run
def run_group_in_thread(run_group_id: str) -> None:
    """Execute a queued run group on a dedicated daemon thread."""

    def _worker() -> None:
        try:
            asyncio.run(execute_run_group(run_group_id))
        except Exception as e:  # noqa: BLE001 - a thread must never die silently
            logger.error("Workflow test run group %s failed: %s", run_group_id, e, exc_info=True)

    threading.Thread(
        target=_worker, daemon=True, name=f"WorkflowTests-{run_group_id[:8]}"
    ).start()


async def execute_run_group(run_group_id: str) -> None:
    """Run every queued case in ``run_group_id``, honoring the concurrency limit."""
    from app.db.session import get_lakebase_session
    from app.db.workflow import WorkflowModel
    from app.db.workflow_test import WorkflowTestRunModel

    db = get_lakebase_session()
    try:
        runs = (
            db.query(WorkflowTestRunModel)
            .filter(WorkflowTestRunModel.run_group_id == run_group_id)
            .all()
        )
        if not runs:
            logger.warning("Workflow test group %s has no runs", run_group_id)
            return
        workflow = (
            db.query(WorkflowModel)
            .filter(WorkflowModel.id == runs[0].workflow_id)
            .first()
        )
        if workflow is None:
            for run in runs:
                _fail_run(db, run, "The workflow was deleted before the tests could run.")
            return
        # Snapshot what the agent needs while we still hold this session: the
        # per-case coroutines open their own sessions, and passing a detached ORM
        # object across them is how you get a DetachedInstanceError mid-run.
        spec = {
            "key": workflow.key,
            "name": workflow.name,
            "goal": workflow.goal,
            "instructions_markdown": workflow.instructions_markdown,
            "allowed_tools": list(workflow.allowed_tools or []),
            "request_type": workflow.request_type,
            "graph_spec": workflow.graph_spec,
        }
        run_ids = [r.id for r in runs]
    finally:
        db.close()

    limit = max(1, int(getattr(settings, "WORKFLOW_TEST_CONCURRENCY", 2) or 2))
    semaphore = asyncio.Semaphore(limit)

    async def _one(run_id: str) -> None:
        async with semaphore:
            await _execute_run(run_id, spec)

    await asyncio.gather(*(_one(rid) for rid in run_ids), return_exceptions=True)
    logger.info("Workflow test group %s finished (%d cases)", run_group_id, len(run_ids))


async def _execute_run(run_id: str, spec: Dict[str, Any]) -> None:
    """Run one case end to end and persist its verdict."""
    from app.db.session import get_lakebase_session
    from app.db.workflow_test import WorkflowTestRunModel

    db = get_lakebase_session()
    started = time.monotonic()
    try:
        run = (
            db.query(WorkflowTestRunModel)
            .filter(WorkflowTestRunModel.id == run_id)
            .first()
        )
        if run is None:
            return
        run.status = "running"
        db.commit()

        question = run.question or ""
        expected = run.expected_outcome or ""
        timeout = max(30, int(getattr(settings, "WORKFLOW_TEST_TIMEOUT_SECONDS", 180) or 180))

        try:
            outcome = await asyncio.wait_for(
                _run_agent(spec, question), timeout=timeout
            )
        except asyncio.TimeoutError:
            _fail_run(
                db, run,
                f"The agent did not finish within {timeout}s. Raise the per-case "
                f"timeout, or simplify the case.",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return
        except Exception as e:  # noqa: BLE001 - report, never crash the group
            logger.error("Workflow test %s: agent run failed: %s", run_id, e, exc_info=True)
            _fail_run(db, run, f"The agent run failed: {e}",
                      duration_ms=int((time.monotonic() - started) * 1000))
            return

        run.transcript = outcome["transcript"]
        run.tool_calls = outcome["tool_calls"]

        try:
            verdict = await judge_case(
                question=question,
                expected=expected,
                answer=outcome["answer"],
                tool_calls=outcome["tool_calls"],
                transcript=outcome["transcript"],
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Workflow test %s: judge failed: %s", run_id, e, exc_info=True)
            # The agent output is still worth keeping — an author can read the
            # transcript and decide for themselves while the judge is broken.
            _fail_run(db, run, f"The judge could not score this run: {e}",
                      duration_ms=int((time.monotonic() - started) * 1000))
            return

        run.status = "complete"
        run.verdict = verdict.get("verdict")
        run.score = verdict.get("score")
        run.rationale = verdict.get("rationale")
        run.missing = verdict.get("missing") or []
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.completed_at = datetime.utcnow()
        db.commit()
        logger.info(
            "Workflow test %s: verdict=%s score=%s", run_id, run.verdict, run.score
        )
    finally:
        db.close()


def _fail_run(db, run, error: str, *, duration_ms: Optional[int] = None) -> None:
    run.status = "error"
    run.error = error
    run.duration_ms = duration_ms
    run.completed_at = datetime.utcnow()
    db.commit()


# ------------------------------------------------------------------------- agent
async def _run_agent(spec: Dict[str, Any], question: str) -> Dict[str, Any]:
    """Run the real agent against ``question`` with mutating tools sandboxed."""
    from app.agents.runner import AgentRunner

    tools = _resolve_test_tools(
        spec.get("allowed_tools") or [], spec.get("graph_spec"),
    )
    runner = AgentRunner(
        tools=tools,
        user_identity=_test_identity(),
        max_iterations=int(getattr(settings, "AGENT_MAX_ITERATIONS", 5) or 5),
        mode="self_service",
        # The whole point: nothing this agent calls may write.
        dry_run=True,
    )
    # Point the agent at THIS workflow. Without it the agent would first have to
    # discover the workflow, and a case would be testing the router instead of the
    # workflow the author is looking at.
    runner.system_prompt += _workflow_focus_block(spec)

    result = await runner.run(query=question)
    answer = result.get("content") or ""
    return {
        "answer": answer,
        "tool_calls": _summarize_tool_calls(result.get("tool_calls") or []),
        "transcript": _summarize_transcript(result.get("messages") or [], answer=answer),
    }


def _test_identity() -> Dict[str, str]:
    """A clearly synthetic requester.

    Deliberately not the admin who clicked Run: a test must not resolve to a real
    person's manager, groups, or entitlements, or its result would depend on who
    happened to launch it.
    """
    return {
        "email": "workflow-test@sandbox.invalid",
        "name": "Workflow Test User",
        "roles": "User",
        "entitlements": "",
    }


# The tools a self-service turn needs regardless of what the workflow allows:
# reading its own playbook, and submitting the request. Without `execute_workflow`
# the agent can gather inputs perfectly and then have no way to finish, so every
# happy-path case would fail for a reason that has nothing to do with the workflow.
# It is mutating, so the sandbox simulates it and the judge sees the arguments.
_ESSENTIAL_TEST_TOOLS = ("get_workflow_instructions", "execute_workflow")


def _resolve_test_tools(
    allowed_tools: List[str], graph_spec: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """The workflow's own tools, plus the ones any self-service turn needs.

    Narrowing to the workflow's tools is what makes a case a test *of this
    workflow*: if it forgot to allow the tool it needs, the test should fail the
    way a real request would rather than quietly succeed using something else. Step
    tools from the graph are included because ``allowed_tools`` is often left unset
    on graph-driven workflows — an empty tool list would make every case fail
    identically and prove nothing.
    """
    from app.tools import catalog

    names: List[str] = []

    def _add(name: Optional[str]) -> None:
        if name and name not in names:
            names.append(name)

    for name in allowed_tools or []:
        _add(name)
    for stage in ((graph_spec or {}).get("stages") or []):
        if isinstance(stage, dict) and stage.get("kind") == "step":
            _add(stage.get("tool"))
    for name in _ESSENTIAL_TEST_TOOLS:
        _add(name)

    resolved: List[Any] = []
    for name in names:
        tool = catalog.get_by_name(name)
        if tool is None:
            logger.warning("Workflow test: tool '%s' is not in the catalog", name)
            continue
        resolved.append(tool)
    return resolved


def _workflow_focus_block(spec: Dict[str, Any]) -> str:
    instructions = (spec.get("instructions_markdown") or "").strip()
    lines = [
        "\n\n## WORKFLOW UNDER TEST",
        f"You are handling the '{spec.get('name') or spec.get('key')}' workflow "
        f"(key: {spec.get('key')}).",
    ]
    if spec.get("goal"):
        lines.append(f"Goal: {spec['goal']}")
    if instructions:
        lines.append(
            "Follow these workflow instructions exactly — they are the runtime "
            "playbook for this request:\n\n" + instructions
        )
    else:
        lines.append(
            "This workflow has NO instructions authored. Do the best you can from "
            "its goal alone."
        )
    return "\n".join(lines)


# ------------------------------------------------------------------------- judge
async def judge_case(
    *,
    question: str,
    expected: str,
    answer: str,
    tool_calls: List[Dict[str, Any]],
    transcript: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Score one case with the LLM judge.

    Goes through ``AgentLLMClient`` — the same client the chat agent uses — so the
    judge reaches whatever model the admin configured, gateway or direct. It
    reuses the agent's endpoint rather than introducing a separate judge model
    setting (per the no-code config rule).
    """
    from app.model_serving.agent_llm import AgentLLMClient

    prompt = _JUDGE_PROMPT.format(
        question=(question or "")[:2000],
        expected=(expected or "")[:2000],
        tools=_render_tools_for_judge(tool_calls)[:_MAX_TOOLS_CHARS],
        answer=(answer or "(the agent produced no final answer)")[:_MAX_ANSWER_CHARS],
        transcript=_render_transcript_for_judge(transcript)[:_MAX_TRANSCRIPT_CHARS],
    )

    try:
        client = AgentLLMClient()
    except ValueError as e:
        raise RuntimeError(f"{e}, so tests cannot be judged.") from e

    # Deterministic as the endpoint allows: a rerun that flips verdicts for no
    # reason destroys trust in the whole tab.
    content = await client.complete_text(prompt, temperature=0.0, max_tokens=800)
    return _parse_verdict(content)


def _parse_verdict(content: str) -> Dict[str, Any]:
    """Parse the judge's JSON, tolerating a code fence or surrounding prose."""
    raw = (content or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"The judge did not return JSON: {raw[:200]!r}")
    data = json.loads(raw[start:end + 1])

    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "partial", "fail"):
        verdict = "partial"
    score = data.get("score")
    try:
        score = max(0, min(100, int(round(float(score)))))
    except (TypeError, ValueError):
        score = None
    missing = data.get("missing")
    if not isinstance(missing, list):
        missing = [str(missing)] if missing else []
    return {
        "verdict": verdict,
        "score": score,
        "rationale": str(data.get("rationale") or "").strip()
        or "The judge returned no rationale.",
        "missing": [str(m) for m in missing if str(m).strip()],
    }


# --------------------------------------------------------------------- summaries
def _summarize_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for call in tool_calls:
        fn = call.get("function") or {}
        out.append({
            "id": call.get("id"),
            "name": fn.get("name"),
            "arguments": fn.get("arguments") or {},
        })
    return out


def _summarize_transcript(
    messages: List[Dict[str, Any]], *, answer: str = "",
) -> List[Dict[str, Any]]:
    """Trim the runner's message list to reviewable evidence.

    The system prompt is dropped (it's the workflow's own instructions, already on
    screen) and tool output is truncated, so a run row stays a readable record
    instead of a megabyte of JSON.

    ``answer`` is appended when the runner's history doesn't already end with it.
    The runner returns the final reply outside ``messages``, so without this the
    stored transcript ends on the user's question — hiding the one thing anyone
    reviewing a verdict most needs to read.
    """
    out: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue
        entry: Dict[str, Any] = {"role": role}
        content = msg.get("content")
        if isinstance(content, str):
            entry["content"] = content[:4000]
        elif content is not None:
            entry["content"] = json.dumps(content, default=str)[:4000]
        if msg.get("name"):
            entry["name"] = msg["name"]
        calls = msg.get("tool_calls")
        if calls:
            entry["tool_calls"] = [
                {
                    "name": ((c or {}).get("function") or {}).get("name"),
                    "arguments": ((c or {}).get("function") or {}).get("arguments"),
                }
                for c in calls
            ]
        out.append(entry)
    if answer:
        last = out[-1] if out else {}
        already_there = (
            last.get("role") == "assistant"
            and (last.get("content") or "").strip() == answer.strip()[:4000]
        )
        if not already_there:
            out.append({"role": "assistant", "content": answer[:4000]})
    return out


def _render_tools_for_judge(tool_calls: List[Dict[str, Any]]) -> str:
    if not tool_calls:
        return "(the agent called no tools)"
    lines = []
    for idx, call in enumerate(tool_calls, start=1):
        args = call.get("arguments")
        args_text = json.dumps(args, default=str) if args else "{}"
        lines.append(f"{idx}. {call.get('name')}({args_text})")
    return "\n".join(lines)


def _render_transcript_for_judge(transcript: List[Dict[str, Any]]) -> str:
    if not transcript:
        return "(empty transcript)"
    lines = []
    for msg in transcript:
        role = msg.get("role") or "?"
        content = msg.get("content") or ""
        if msg.get("tool_calls") and not content:
            content = "called: " + ", ".join(
                str(c.get("name")) for c in msg["tool_calls"]
            )
        label = f"{role}" + (f" [{msg['name']}]" if msg.get("name") else "")
        lines.append(f"{label}: {content}")
    return "\n\n".join(lines)
