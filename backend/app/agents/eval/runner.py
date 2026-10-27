"""Run the assistant test library against the real agent and judge the results.

The execution model is the one the Tests tab uses for workflows
(``app.workflows.test_runner``): start the real agent, simulate every mutating
tool (``dry_run``), then have an LLM judge compare the transcript to a
plain-English expectation. It differs in three ways that suit testing the whole
assistant:

* the agent gets the tools and prompt a real user of that role would get, not a
  single workflow's narrowed set;
* a case can be several turns, because behaviors like "ask before filing" only
  show up across a reply;
* deterministic checks (``checks.py``) run first and fail a case on their own, so
  two runs can be compared without trusting the judge to be consistent.

The requester is synthetic by default, so a test never resolves to a real
person's groups. Cases marked ``needs_identity`` exercise tools that act as the
caller (queries, permissions); they are skipped unless the caller supplies their
own Databricks token and email, and are reported as skipped, never as passed.

The per-user context block (roles, open requests, group memberships) the live chat
injects is built only when a real identity is supplied; the synthetic requester has
none, so without ``--obo-profile`` the assistant has to look such things up itself.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import AbstractSet, Any, Awaitable, Callable, Dict, List, Optional, Sequence, Union

from app.agents.eval.checks import evaluate_checks, failed_checks
from app.agents.eval.library import EvalCase

logger = logging.getLogger(__name__)

DEFAULT_EMAIL = "assistant-eval@sandbox.invalid"
DEFAULT_TIMEOUT_SECONDS = 300
SURFACE = "edh"

# The judge has to check the answer against what the tools returned, so it gets far
# more of each tool result than the studio's run rows keep. A cut-off result makes
# a true statement look invented.
_EVIDENCE_PER_MESSAGE = 20000
_JUDGE_TRANSCRIPT_CHARS = 60000

# Replies the agent produces when the model could not be reached. They look like
# answers, so without this a rate limit would be graded as the assistant giving a
# bad answer, and a run during a busy minute would "regress" for no reason.
_INFRASTRUCTURE_FAILURES = (
    "error connecting to the model",
    "encountered an error processing your request",
)
DEFAULT_RETRIES = 2
# Each chat turn sends the whole standing prompt, so parallel cases hit the model's
# input-tokens-per-minute limit quickly. Two keeps a full run well inside it.
DEFAULT_CONCURRENCY = 2
RETRY_BACKOFF_SECONDS = 20

_JUDGE_PROMPT = """You are grading one scripted conversation with a governed \
self-service data platform's AI assistant. A reviewer wrote what the user says \
and, in plain English, what the assistant should do. Decide whether the \
assistant's actual behavior matches.

Grade BEHAVIOR, not wording. The assistant passes when it does the substance of \
what was expected: it uses the right kind of tool, tells the user only what the \
tool results support, asks when something required is missing, and stops when it \
should wait for the user.

Context for grading:
- Tools that change something ran in a SANDBOX. A result containing \
"dry_run": true means the call was correct and simply not executed. Treat it as \
a successful call. An answer that reports the action as filed, submitted or \
prepared is right, and so is one that says it was only simulated; do not mark \
the assistant down for either.
- Most data tools run AS the signed-in user, so a permission error in a tool \
result (for example "does not have USE SCHEMA") is about the user's own access. \
The assistant should say "you don't have access", and it is not wrong to say so.
- Some tools act as the user and need their own Databricks identity, which this \
test run may not have. When such a tool fails, the assistant is right to say it \
could not check. It is wrong to claim a result the tool did not give it, or to \
describe the failure in terms of tokens, hosts or other internals.
- Asking a clarifying question is CORRECT when the expectation says required \
information is missing. Fabricating a value the user never supplied, or inventing \
a fact the tools never returned, is a failure even if the answer reads well.
- When the assistant calls ask_your_data, Genie writes the answer and the user is \
shown it as-is, so the final answer is Genie's own text and the tool entry in the \
transcript is what Genie returned (its SQL and rows). Grade how the assistant used \
Genie: the question it sent, the conversation it continued, and not working around \
it. Do not grade Genie's wording or formatting.
- Judge only what the expectation covers. Ignore tone and formatting unless the \
expectation mentions them.

Return ONLY compact JSON, no prose and no code fence:
{{"verdict": "pass" | "partial" | "fail", "score": <0-100>, \
"rationale": "<2-3 sentences citing what the assistant actually did>", \
"missing": ["<expectation not met>", ...]}}

"score" is your confidence that the expected outcome was met (100 = fully met). \
"missing" is empty on a clean pass.

WHAT THE USER SAID (one line per turn):
{turns}

EXPECTED OUTCOME:
{expected}

WHAT THE ASSISTANT ACTUALLY DID
Tools called (in order):
{tools}

Final answer to the user:
{answer}

Full transcript:
{transcript}
"""

AgentFn = Callable[..., Awaitable[Dict[str, Any]]]
JudgeFn = Callable[..., Awaitable[Dict[str, Any]]]


# ---------------------------------------------------------------------- setup
def synthetic_user(email: str, role: str, entitlements: Sequence[str] = ()):
    from app.models.user import User

    return User(
        id=f"eval-{role.lower().replace(' ', '-')}",
        email=email,
        full_name="Assistant Eval User",
        roles=[role],
        entitlements=list(entitlements),
    )


def resolve_entitlements(email: str, token: Optional[str]) -> List[str]:
    """The groups the app would see for this person at login (SCIM, via their own token).

    Without them the assistant's identity has no groups, so "what groups am I in" and
    every access check that starts from the user's groups answers about a person who
    belongs to nothing. Synthetic runs (no token) legitimately have none.
    """
    if not token:
        return []
    from app.api.deps import _get_user_entitlements

    try:
        return list(_get_user_entitlements(email, token))
    except Exception as e:  # noqa: BLE001 - fall back to "no groups", as the app would
        logger.warning("Could not resolve groups for %s: %s", email, e)
        return []


def published_workflow_keys() -> AbstractSet[str]:
    """Keys of the workflows the assistant can currently route to (published, not disabled)."""
    from app.db.session import get_session_local
    from app.services.workflow_service import WorkflowService

    db = get_session_local()()
    try:
        return {w.key for w in WorkflowService.list_published(db)}
    finally:
        db.close()


def resolve_tools(role: str, email: str = DEFAULT_EMAIL) -> List[Any]:
    """The tools the assistant surface exposes to a user holding ``role``."""
    from app.db.session import get_lakebase_session
    from app.services.tool_registry_service import ToolRegistryService

    db = get_lakebase_session()
    try:
        return ToolRegistryService.resolve_tools_for_surface(
            db, SURFACE, synthetic_user(email, role)
        )
    finally:
        db.close()


def _identity(email: str, role: str, entitlements: Sequence[str] = ()) -> Dict[str, str]:
    # The chat endpoint joins entitlements into one string the same way.
    return {
        "email": email,
        "name": "Assistant Eval User",
        "roles": role,
        "entitlements": ", ".join(entitlements),
    }


def prompt_footprint(tools: Sequence[Any], email: str = DEFAULT_EMAIL, role: str = "User") -> Dict[str, Any]:
    """Size of what the model is sent on every call: system prompt + tool schemas.

    Recorded in each report so a prompt or tool-surface change shows up as a number
    next to the pass rate. Tokens use a stand-in tokenizer, so compare runs with
    each other rather than reading them as the model's own count.
    """
    from app.agents.runner import AgentRunner

    runner = AgentRunner(tools=list(tools), user_identity=_identity(email, role), mode="unified")
    system_prompt = runner.system_prompt
    schemas = json.dumps(runner._format_tools_for_llm(list(tools)) or [])
    out: Dict[str, Any] = {
        "tool_count": len(tools),
        "system_prompt_chars": len(system_prompt),
        "tool_schema_chars": len(schemas),
    }
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        out["system_prompt_tokens"] = len(enc.encode(system_prompt))
        out["tool_schema_tokens"] = len(enc.encode(schemas))
        out["standing_tokens"] = out["system_prompt_tokens"] + out["tool_schema_tokens"]
    except Exception:  # noqa: BLE001 - tokenizer is optional; chars still compare
        out["standing_tokens"] = (len(system_prompt) + len(schemas)) // 4
    return out


# ---------------------------------------------------------------------- genie
# ``ask_your_data`` does not answer inside the agent loop. It hands the browser a
# conversation to poll, the browser shows Genie's written answer as-is, and on the
# next turn replays the exchange (with the conversation id) as history. Without the
# same steps here a Genie case would end on "Asking Genie..." and could say nothing
# about what the user actually saw.
GENIE_KIND = "genie"
GENIE_POLL_TIMEOUT_SECONDS = 240
_GENIE_ROWS_IN_EVIDENCE = 5

GeniePollFn = Callable[..., Awaitable[Any]]


async def drain_genie_poll(
    ids: Dict[str, Any],
    *,
    obo_token: Optional[str],
    email: str,
    role: str,
    timeout: float = GENIE_POLL_TIMEOUT_SECONDS,
    poll_fn: Optional[GeniePollFn] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> Dict[str, Any]:
    """Poll one Genie conversation until it settles, as the chat UI does.

    Goes through the same endpoint function the browser calls, so the rows,
    ``incomplete`` handling and conversation id are the ones a user would get.
    Returns ``{"status": "complete", "result": {...}}``, or ``{"status": "failed" |
    "timeout", "error": "..."}``.
    """
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.v1 import agent_polls

    body = agent_polls.GeniePollRequest(
        space_id=ids.get("space_id") or None,
        conversation_id=str(ids.get("conversation_id") or ""),
        message_id=str(ids.get("message_id") or ""),
        question=ids.get("question"),
    )
    request = SimpleNamespace(state=SimpleNamespace(token=obo_token))
    user = synthetic_user(email, role)
    poll = poll_fn or agent_polls.poll_genie
    deadline = time.monotonic() + timeout
    while True:
        try:
            polled = await poll(body, request, user)
        except HTTPException as e:
            return {"status": "failed", "error": str(e.detail)}
        if polled.status == "complete":
            return {"status": "complete", "result": polled.result or {}}
        if polled.status == "failed":
            return {"status": "failed", "error": polled.error or "Genie failed."}
        if time.monotonic() >= deadline:
            return {"status": "timeout", "error": f"Genie had not answered after {int(timeout)}s."}
        await sleep((polled.attempt_after_ms or 3000) / 1000)


def genie_result_table(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The first result table in a settled Genie answer: ``{columns, rows, total}``.

    Reads ``attachments[].query.result`` the way the chat's ``parseGenieResult`` does;
    columns arrive as names or as ``{name, type_text}`` objects.
    """
    for attachment in result.get("attachments") or []:
        query = attachment.get("query") if isinstance(attachment, dict) else None
        data = query.get("result") if isinstance(query, dict) else None
        if not isinstance(data, dict):
            continue
        columns = [c.get("name") if isinstance(c, dict) else c for c in data.get("columns") or []]
        rows = data.get("rows")
        if columns and isinstance(rows, list) and rows:
            return {
                "columns": [str(c) for c in columns],
                "rows": rows,
                "total": data.get("total_row_count") or len(rows),
            }
    return None


def genie_answer(settled: Dict[str, Any]) -> str:
    """What the user is shown for a settled Genie conversation."""
    if settled.get("status") != "complete":
        return f"Genie couldn't answer this. {settled.get('error') or ''}".strip()
    result = settled.get("result") or {}
    for key in ("final_answer", "text", "answer", "description"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Genie finished but returned no written answer."


def genie_tool_message(question: str, settled: Dict[str, Any]) -> str:
    """The tool message the chat replays into history after a Genie answer.

    Kept word for word with ``buildHistory`` in ``ChatView.tsx`` (a unit test checks
    the follow-up sentence), because the notes are what let the model chart the rows
    and continue the same Genie conversation on a later turn.
    """
    result = settled.get("result") or {}
    content = question or "Genie answered"
    table = genie_result_table(result)
    if table:
        content += (
            f" [Result columns: {', '.join(table['columns'])} ({len(table['rows'])} rows)."
            " To visualize, call render_chart with these exact column names.]"
        )
    conversation_id = result.get("conversation_id")
    if isinstance(conversation_id, str) and conversation_id:
        content += (
            f" [Genie conversation_id: {conversation_id}."
            " For a follow-up to this answer, pass it as conversation_id to ask_your_data.]"
        )
    return content


def genie_evidence(settled: Dict[str, Any]) -> str:
    """What Genie returned, for the judge: the status, the SQL it ran and the first rows."""
    if settled.get("status") != "complete":
        return f"Genie {settled.get('status')}: {settled.get('error')}"
    result = settled.get("result") or {}
    parts = [f"Genie answered (conversation_id={result.get('conversation_id')})."]
    for item in (result.get("query_items") or [])[:3]:
        if isinstance(item, dict) and item.get("sql"):
            parts.append(f"SQL: {item['sql']}")
    table = genie_result_table(result)
    if table:
        shown = table["rows"][:_GENIE_ROWS_IN_EVIDENCE]
        parts.append(
            f"Columns: {', '.join(table['columns'])}. {table['total']} row(s); "
            f"first {len(shown)}: {json.dumps(shown, default=str)}"
        )
    else:
        parts.append("No result rows came back with the answer.")
    return " ".join(parts)


def genie_turn_records(
    question: str,
    pending: Dict[str, Any],
    settled: Dict[str, Any],
    answer: str,
    arguments: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """History and transcript entries for one Genie turn.

    History is shaped as the browser replays it: the assistant's tool call, the tool
    message carrying the conversation id, then Genie's written answer.
    """
    tool_call_id = str(pending.get("tool_call_id") or "genie-call")
    tool_name = str(pending.get("tool_name") or "ask_your_data")
    asked = (arguments.get("question") if isinstance(arguments.get("question"), str) else "") or question
    history: List[Dict[str, Any]] = [
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(arguments, default=str)},
                }
            ],
        },
        {
            "role": "tool",
            "content": genie_tool_message(asked, settled),
            "tool_call_id": tool_call_id,
            "name": tool_name,
        },
        {"role": "assistant", "content": answer},
    ]
    transcript: List[Dict[str, Any]] = [
        {"role": "tool", "name": tool_name, "content": genie_evidence(settled)[:_EVIDENCE_PER_MESSAGE]},
        {"role": "assistant", "content": answer[:_EVIDENCE_PER_MESSAGE]},
    ]
    return {"history": history, "transcript": transcript}


# ---------------------------------------------------------------------- agent
async def run_agent(
    case: EvalCase,
    *,
    tools: Sequence[Any],
    email: str = DEFAULT_EMAIL,
    obo_token: Optional[str] = None,
    user_context_block: Optional[str] = None,
    entitlements: Sequence[str] = (),
) -> Dict[str, Any]:
    """Play every turn of ``case`` through the real agent, mutating tools simulated."""
    from app.agents.runner import AgentRunner
    from app.core.config import settings
    from app.workflows.test_runner import _summarize_tool_calls, _summarize_transcript

    history: List[Dict[str, str]] = []
    tool_calls: List[Dict[str, Any]] = []
    transcript: List[Dict[str, Any]] = []
    answer = ""
    for turn_index, question in enumerate(case.turns):
        runner = AgentRunner(
            tools=list(tools),
            user_identity=_identity(email, case.role, entitlements),
            max_iterations=int(getattr(settings, "AGENT_MAX_ITERATIONS", 10) or 10),
            # The mode the chat endpoint uses, so the prompt matches production.
            mode="unified",
            user_context_block=user_context_block,
            # Nothing this agent calls may write.
            dry_run=True,
        )
        result = await runner.run(query=question, history=history or None, obo_token=obo_token)
        answer = result.get("content") or ""
        turn_calls = _summarize_tool_calls(result.get("tool_calls") or [])
        for call in turn_calls:
            call["turn"] = turn_index + 1
            tool_calls.append(call)

        pending = result.get("pending_poll")
        genie: Optional[Dict[str, List[Dict[str, Any]]]] = None
        if isinstance(pending, dict) and pending.get("kind") == GENIE_KIND:
            settled = await drain_genie_poll(
                pending.get("ids") or {}, obo_token=obo_token, email=email, role=case.role
            )
            answer = genie_answer(settled)
            call = next((c for c in turn_calls if c.get("id") == pending.get("tool_call_id")), {})
            genie = genie_turn_records(question, pending, settled, answer, call.get("arguments") or {})

        # The runner returns the whole conversation every turn; keep only what
        # came after this turn's question so a multi-turn transcript reads once.
        turn_msgs = _summarize_transcript(
            result.get("messages") or [],
            answer="" if genie else answer,
            content_limit=_EVIDENCE_PER_MESSAGE,
        )
        transcript.append({"role": "user", "content": question, "turn": turn_index + 1})
        transcript.extend(_after_last_user_message(turn_msgs))
        if genie:
            transcript.extend(genie["transcript"])
            history.extend(genie["history"])
        else:
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": answer})
    return {"answer": answer, "tool_calls": tool_calls, "transcript": transcript}


def _after_last_user_message(summary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The assistant/tool messages of the current turn (everything after the last user message)."""
    start = 0
    for i, msg in enumerate(summary):
        if msg.get("role") == "user":
            start = i + 1
    return summary[start:]


# ---------------------------------------------------------------------- judge
async def judge(
    case: EvalCase,
    *,
    answer: str,
    tool_calls: List[Dict[str, Any]],
    transcript: List[Dict[str, Any]],
) -> Dict[str, Any]:
    from app.model_serving.agent_llm import AgentLLMClient
    from app.workflows.test_runner import (
        _MAX_ANSWER_CHARS,
        _MAX_TOOLS_CHARS,
        _parse_verdict,
        _render_tools_for_judge,
        _render_transcript_for_judge,
    )

    prompt = _JUDGE_PROMPT.format(
        turns="\n".join(f"{i}. {t}" for i, t in enumerate(case.turns, 1))[:2000],
        expected=case.expected[:2000],
        tools=_render_tools_for_judge(tool_calls)[:_MAX_TOOLS_CHARS],
        answer=(answer or "(the assistant produced no final answer)")[:_MAX_ANSWER_CHARS],
        transcript=_render_transcript_for_judge(transcript)[:_JUDGE_TRANSCRIPT_CHARS],
    )
    # Same client the agent uses: the judge reaches whatever model the admin
    # configured, so there is no separate judge setting to drift.
    content = await AgentLLMClient().complete_text(prompt, temperature=0.0, max_tokens=800)
    return _parse_verdict(content)


# ------------------------------------------------------------------- one case
def is_infrastructure_failure(answer: str) -> bool:
    """True when ``answer`` is the agent's "could not reach the model" reply."""
    text = (answer or "").lower()
    return any(marker in text for marker in _INFRASTRUCTURE_FAILURES)


# What tools return when the caller's Databricks token has lapsed. The assistant then
# reports, correctly, that it could not check anything, which reads like a bad answer;
# but the failure is the run's, so it is retried with a fresh token and never graded.
# Genie surfaces the same lapse as an opaque transport error rather than a 403.
_AUTH_FAILURES = (
    "invalid token",
    "invalid access token",
    "unhandled errors in a taskgroup",
)


def is_auth_failure(transcript: Sequence[Dict[str, Any]]) -> bool:
    """True when any tool result in ``transcript`` shows the caller's token was rejected."""
    for message in transcript or []:
        if message.get("role") != "tool":
            continue
        text = str(message.get("content") or "").lower()
        if any(marker in text for marker in _AUTH_FAILURES):
            return True
    return False


async def build_user_context_block(
    email: str, role: str, entitlements: Sequence[str] = ()
) -> Optional[str]:
    """The per-user context the live chat injects (roles, groups, open requests).

    Only meaningful for a real identity, so the caller asks for it only then.
    """
    from app.core.feature_flags import is_feature_enabled

    if not is_feature_enabled("user_context"):
        return None
    from app.db.session import get_lakebase_session
    from app.services.user_context import get_user_context, render_user_context_block

    db = get_lakebase_session()
    try:
        payload = await get_user_context(db, synthetic_user(email, role, entitlements))
        return render_user_context_block(payload)
    except Exception as e:  # noqa: BLE001 - the chat itself never fails a turn over this
        logger.warning("Could not build the user context for %s: %s", email, e)
        return None
    finally:
        db.close()


def _base_result(case: EvalCase) -> Dict[str, Any]:
    return {
        "id": case.id,
        "suite": case.suite,
        "name": case.name,
        "tags": case.tags,
        "role": case.role,
        "turns": case.turns,
        "expected": case.expected,
        "needs_identity": case.needs_identity,
    }


def _status(verdict: Optional[str], checks: List[Dict[str, Any]], judged: bool) -> str:
    if failed_checks(checks):
        return "fail"
    if not judged:
        return "pass"
    return verdict if verdict in ("pass", "partial", "fail") else "partial"


async def run_case(
    case: EvalCase,
    *,
    tools: Sequence[Any],
    email: str = DEFAULT_EMAIL,
    obo_token: Optional[str] = None,
    use_judge: bool = True,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    user_context_block: Optional[str] = None,
    entitlements: Sequence[str] = (),
    published_workflows: Optional[AbstractSet[str]] = None,
    retries: int = DEFAULT_RETRIES,
    retry_backoff: float = RETRY_BACKOFF_SECONDS,
    agent: Optional[AgentFn] = None,
    judge_fn: Optional[JudgeFn] = None,
    renew_token: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Run one case end to end and return a JSON-serializable result.

    If the model could not be reached (a rate limit, say) the case is retried after
    a pause, and reported as ``error`` rather than graded if it still fails: the
    assistant did not give a bad answer, it gave none. The same goes for a caller's
    token that was rejected mid-case; ``renew_token`` mints a new one for the retry.

    ``agent`` and ``judge_fn`` exist so the plumbing can be tested without a model.
    """
    result = _base_result(case)
    if published_workflows is not None:
        missing = [w for w in case.requires_workflows if w not in published_workflows]
        if missing:
            result.update(
                status="skipped",
                error=f"Needs workflow(s) {', '.join(missing)} published, and they are not in this environment.",
            )
            return result
    if case.requires_tools:
        offered = {getattr(t, "name", None) for t in tools}
        lacking = [t for t in case.requires_tools if t not in offered]
        if lacking:
            result.update(
                status="skipped",
                error=(
                    f"Needs tool(s) {', '.join(lacking)}, which this role is not given in this "
                    "environment (an admin has them switched off)."
                ),
            )
            return result
    if case.needs_identity and not obo_token:
        result.update(
            status="skipped",
            error="Needs the caller's own Databricks identity; re-run with --obo-profile and --email.",
        )
        return result

    started = time.monotonic()
    attempt = 0
    auth_failed = False
    while True:
        attempt += 1
        try:
            outcome = await asyncio.wait_for(
                (agent or run_agent)(
                    case,
                    tools=tools,
                    email=email,
                    obo_token=obo_token,
                    user_context_block=user_context_block,
                    entitlements=entitlements,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            result.update(
                status="error", attempts=attempt, error=f"The assistant did not finish within {timeout}s."
            )
            return result
        except Exception as e:  # noqa: BLE001 - one bad case must not end the run
            logger.error("Eval case %s: agent run failed: %s", case.id, e, exc_info=True)
            result.update(status="error", attempts=attempt, error=f"The agent run failed: {e}")
            return result
        auth_failed = bool(obo_token) and is_auth_failure(outcome.get("transcript") or [])
        if not (is_infrastructure_failure(outcome["answer"]) or auth_failed) or attempt > retries:
            break
        if auth_failed:
            # A fresh token will not take a rate limit's pause, so go straight back in.
            logger.warning(
                "Eval case %s: the caller's token was rejected (attempt %d of %d); retrying%s",
                case.id, attempt, retries + 1, " with a new token" if renew_token else "",
            )
            if renew_token:
                obo_token = await asyncio.to_thread(renew_token)
            continue
        logger.warning(
            "Eval case %s: model unreachable (attempt %d of %d); retrying in %ss",
            case.id, attempt, retries + 1, retry_backoff * attempt,
        )
        await asyncio.sleep(retry_backoff * attempt)
    result["attempts"] = attempt

    answer = outcome["answer"]
    tool_calls = outcome["tool_calls"]
    checks = evaluate_checks(case, [c.get("name") for c in tool_calls], answer, tool_calls)
    result.update(
        answer=answer,
        tool_calls=tool_calls,
        transcript=outcome["transcript"],
        checks=checks,
    )

    if auth_failed:
        result.update(
            status="error",
            error=(
                f"The caller's Databricks token was rejected in {attempt} attempts, so tools that act as "
                "the user failed; this is not a verdict on the assistant. Run `databricks auth login` "
                "for the profile and re-run."
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    if is_infrastructure_failure(answer):
        result.update(
            status="error",
            error=(
                f"The model could not be reached after {attempt} attempts (usually a rate limit); "
                "this is not a verdict on the assistant. Re-run the case, or lower --concurrency."
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    verdict: Dict[str, Any] = {}
    if use_judge:
        try:
            verdict = await (judge_fn or judge)(
                case, answer=answer, tool_calls=tool_calls, transcript=outcome["transcript"]
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Eval case %s: judge failed: %s", case.id, e, exc_info=True)
            # Keep the evidence: the checks and transcript still say a lot.
            result.update(
                status="error",
                error=f"The judge could not score this run: {e}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return result
    result.update(
        status=_status(verdict.get("verdict"), checks, use_judge),
        judge=verdict or None,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result


# ------------------------------------------------------------------ the suite
ProgressFn = Callable[[Dict[str, Any], int, int], None]


async def run_cases(
    cases: Sequence[EvalCase],
    *,
    email: str = DEFAULT_EMAIL,
    obo_token: Union[str, Callable[[], str], None] = None,
    use_judge: bool = True,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    retry_backoff: float = RETRY_BACKOFF_SECONDS,
    with_user_context: Optional[bool] = None,
    repeat: int = 1,
    on_result: Optional[ProgressFn] = None,
    tools_for_role: Optional[Callable[[str], Sequence[Any]]] = None,
    agent: Optional[AgentFn] = None,
    judge_fn: Optional[JudgeFn] = None,
) -> List[Dict[str, Any]]:
    """Run ``cases`` with bounded concurrency and return results in library order.

    ``obo_token`` is the caller's Databricks token, or a callable that returns a current
    one (and may have a ``renew`` method). A run of many cases outlasts a token that was
    fetched once at the start, so a callable is asked again for every case.
    """
    token_source: Optional[Callable[[], str]] = obo_token if callable(obo_token) else None
    first_token: Optional[str] = (
        await asyncio.to_thread(token_source) if token_source else obo_token  # type: ignore[assignment]
    )
    resolver = tools_for_role or (lambda role: resolve_tools(role, email))
    role_tools: Dict[str, Sequence[Any]] = {}
    for case in cases:
        if case.role not in role_tools:
            role_tools[case.role] = resolver(case.role)

    # The live chat knows who is asking; only a real identity has anything to know.
    entitlements = await asyncio.to_thread(resolve_entitlements, email, first_token) if first_token else []
    context_blocks: Dict[str, Optional[str]] = {}
    if (with_user_context if with_user_context is not None else bool(first_token)):
        for role in role_tools:
            context_blocks[role] = await build_user_context_block(email, role, entitlements)

    # Only look when a case depends on it; ``None`` means "do not check" to run_case.
    published: Optional[AbstractSet[str]] = None
    if any(case.requires_workflows for case in cases):
        published = await asyncio.to_thread(published_workflow_keys)

    semaphore = asyncio.Semaphore(max(1, concurrency))
    runs = max(1, repeat)
    jobs = [(case, run) for case in cases for run in range(1, runs + 1)]
    results: Dict[tuple, Dict[str, Any]] = {}
    done = 0

    async def _one(case: EvalCase, run: int) -> None:
        nonlocal done
        async with semaphore:
            # Resolved when the case starts, not when the run did: this one may be minutes in.
            token = await asyncio.to_thread(token_source) if token_source else first_token
            res = await run_case(
                case,
                tools=role_tools[case.role],
                email=email,
                obo_token=token,
                renew_token=getattr(token_source, "renew", None),
                use_judge=use_judge,
                timeout=timeout,
                user_context_block=context_blocks.get(case.role),
                entitlements=entitlements,
                published_workflows=published,
                retries=retries,
                retry_backoff=retry_backoff,
                agent=agent,
                judge_fn=judge_fn,
            )
        res["run"] = run
        results[(case.id, run)] = res
        done += 1
        if on_result:
            on_result(res, done, len(jobs))

    await asyncio.gather(*(_one(c, r) for c, r in jobs))
    return [results[(c.id, r)] for c, r in jobs]


# --------------------------------------------------------------------- report
def summarize(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {"pass": 0, "partial": 0, "fail": 0, "error": 0, "skipped": 0}
    for r in results:
        counts[r.get("status", "error")] = counts.get(r.get("status", "error"), 0) + 1
    # Errors and skips were not graded: the model never answered, or the case was
    # not applicable. They are counted on their own, not as failures of the assistant.
    graded = counts["pass"] + counts["partial"] + counts["fail"]
    return {
        **counts,
        "total": len(results),
        "graded": graded,
        "pass_rate": round(counts["pass"] / graded, 3) if graded else None,
    }


def build_report(
    results: Sequence[Dict[str, Any]],
    *,
    label: str = "",
    footprint: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from app.core.config import settings

    return {
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.AI_GATEWAY_ENDPOINT or settings.MODEL_SERVING_AGENT_LLM_ENDPOINT,
        "reasoning_effort": settings.AGENT_LLM_REASONING_EFFORT or "",
        "options": options or {},
        "footprint": footprint or {},
        "summary": summarize(results),
        "results": list(results),
    }


_ICON = {"pass": "PASS", "partial": "PART", "fail": "FAIL", "error": "ERR ", "skipped": "SKIP"}


def format_result_line(result: Dict[str, Any], done: int, total: int) -> str:
    status = result.get("status", "error")
    run = f" (run {result['run']})" if result.get("run", 1) > 1 else ""
    bits = [f"[{done:>2}/{total}] {_ICON.get(status, status):4} {result['id']}{run}"]
    judge_info = result.get("judge") or {}
    if judge_info.get("score") is not None:
        bits.append(f"score={judge_info['score']}")
    if result.get("duration_ms"):
        bits.append(f"{result['duration_ms'] / 1000:.0f}s")
    bad = failed_checks(result.get("checks") or [])
    if bad:
        bits.append("checks failed: " + "; ".join(b["check"] for b in bad))
    if status in ("error", "skipped") and result.get("error"):
        bits.append(result["error"][:110])
    return "  ".join(bits)


def format_report(report: Dict[str, Any], *, detail: bool = True) -> str:
    s = report["summary"]
    lines = [
        f"Assistant tests {report.get('label') or ''} on {report.get('model')} "
        f"(reasoning={report.get('reasoning_effort') or 'default'}) at {report.get('created_at')}",
        f"  pass {s['pass']}  partial {s['partial']}  fail {s['fail']}  error {s['error']}  "
        f"skipped {s['skipped']}   pass rate {s['pass_rate']}",
    ]
    fp = report.get("footprint") or {}
    if fp:
        lines.append(
            f"  standing context ~{fp.get('standing_tokens')} tokens "
            f"({fp.get('tool_count')} tools; prompt {fp.get('system_prompt_tokens', '?')}, "
            f"schemas {fp.get('tool_schema_tokens', '?')})"
        )
    if detail:
        for r in report["results"]:
            if r["status"] in ("pass", "skipped"):
                continue
            lines.append("")
            run = f" (run {r['run']})" if r.get("run", 1) > 1 else ""
            lines.append(f"  {_ICON[r['status']]}  {r['id']}{run} - {r['name']}")
            if r.get("error"):
                lines.append(f"      error: {r['error']}")
            for c in failed_checks(r.get("checks") or []):
                lines.append(f"      check: {c['check']} -> {c['detail']}")
            j = r.get("judge") or {}
            if j and r["status"] != "pass":
                lines.append(f"      judge: {j.get('rationale')}")
                for m in j.get("missing") or []:
                    lines.append(f"      missing: {m}")
    return "\n".join(lines)


_VALUE = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
# A case "moved" when its score changes by at least this much. One run pass -> partial
# is 0.5, so single runs still surface; with --repeat a single flipped run is below it.
_MOVED = 0.4


def _rollup(results: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per case id: how many runs passed, and a 0..1 score over the graded runs."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        grouped.setdefault(r["id"], []).append(r)
    out: Dict[str, Dict[str, Any]] = {}
    for case_id, runs in grouped.items():
        graded = [r for r in runs if r["status"] in _VALUE]
        out[case_id] = {
            "runs": runs,
            "graded": len(graded),
            "passed": sum(1 for r in graded if r["status"] == "pass"),
            "value": (sum(_VALUE[r["status"]] for r in graded) / len(graded)) if graded else None,
        }
    return out


def _fraction(row: Dict[str, Any]) -> str:
    return f"{row['passed']}/{row['graded']}" if row["graded"] else "n/a"


def compare_reports(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    """A readable before/after: regressions first, then fixes, then the footprint.

    Cases are scored by the share of their graded runs that passed (partial counts
    half), so a run repeated with ``--repeat`` separates a real change from the
    model's run-to-run variation. Compare runs with the same repeat count.
    """
    ra, rb = _rollup(a["results"]), _rollup(b["results"])
    regressions, improvements, same = [], [], 0
    for case_id in sorted(set(ra) & set(rb)):
        va, vb = ra[case_id]["value"], rb[case_id]["value"]
        if va is None or vb is None:
            continue
        if vb <= va - _MOVED:
            regressions.append((case_id, ra[case_id], rb[case_id]))
        elif vb >= va + _MOVED:
            improvements.append((case_id, ra[case_id], rb[case_id]))
        else:
            same += 1
    sa_, sb_ = a["summary"], b["summary"]
    lines = [
        f"A: {a.get('label') or '-'} ({a.get('created_at')})   B: {b.get('label') or '-'} ({b.get('created_at')})",
        f"  pass rate  {sa_['pass_rate']} -> {sb_['pass_rate']}   "
        f"(pass {sa_['pass']} -> {sb_['pass']}, partial {sa_['partial']} -> {sb_['partial']}, "
        f"fail {sa_['fail']} -> {sb_['fail']}, error {sa_['error']} -> {sb_['error']})",
    ]
    fa, fb = a.get("footprint") or {}, b.get("footprint") or {}
    if fa and fb:
        lines.append(
            f"  standing context  ~{fa.get('standing_tokens')} -> ~{fb.get('standing_tokens')} tokens   "
            f"tools {fa.get('tool_count')} -> {fb.get('tool_count')}"
        )
    for title, rows in (("REGRESSIONS", regressions), ("IMPROVEMENTS", improvements)):
        if not rows:
            continue
        lines.append("")
        lines.append(f"{title}:")
        for case_id, before, after in rows:
            lines.append(f"  {case_id}: passed {_fraction(before)} -> {_fraction(after)}")
            if title == "REGRESSIONS":
                worst = next((r for r in after["runs"] if r["status"] in ("fail", "partial")), None)
                if worst:
                    for c in failed_checks(worst.get("checks") or [])[:3]:
                        lines.append(f"      check: {c['check']} -> {c['detail']}")
                    rationale = (worst.get("judge") or {}).get("rationale")
                    if rationale:
                        lines.append(f"      judge: {rationale}")
    lines.append("")
    lines.append(f"unchanged: {same}")
    return "\n".join(lines)
