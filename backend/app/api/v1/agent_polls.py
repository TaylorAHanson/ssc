"""
Poll endpoints for asynchronous agent tools.

When an agent tool returns a *pending-poll envelope*, the agent runner
emits a :class:`~app.agents.events.PendingPollEvent` and stops. The UI
then drains the asynchronous backend (here: Databricks Genie) by
calling the matching poll endpoint repeatedly until it reports
``status: complete`` or ``status: failed``. Once the poll completes the
UI re-invokes the agent runner with a synthetic ``tool`` message
carrying the resolved result so the LLM can summarize the answer.

Each ``kind`` of pending poll lives in its own endpoint here so adding
a new async MCP server (Vector Search, UC Functions, etc.) is just a
matter of writing one more handler with the same wire shape.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.config import settings
from app.models.user import User
from app.providers.databricks_mcp import GenieAuthUnavailableError, call_genie_tool

logger = logging.getLogger(__name__)

router = APIRouter()


class GeniePollRequest(BaseModel):
    """Wire shape sent by the UI when polling a Genie query."""

    space_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional Genie Space ID echoed from the pending_poll event. "
            "Absent / empty means general Databricks Genie."
        ),
    )
    conversation_id: str = Field(..., description="Genie conversation handle.")
    message_id: str = Field(..., description="Genie message / query handle.")
    question: Optional[str] = Field(
        default=None,
        description=(
            "Original user question (optional, echoed back in the response "
            "so the UI can use it as the synthetic-tool message content "
            "without having to remember it locally)."
        ),
    )


PollStatus = Literal["running", "complete", "failed"]


class GeniePollResponse(BaseModel):
    """Wire shape returned to the UI for each poll."""

    status: PollStatus
    result: Optional[Dict[str, Any]] = None
    # In-progress snapshot (same enriched shape as ``result``) returned while
    # ``status == "running"``. Genie streams its answer by re-sending the full
    # ``final_answer`` every poll — the value can grow *and shrink/change*, so
    # the UI must render it by REPLACING the previous snapshot, never appending.
    # Carrying the full enriched payload (not just the text) also lets the UI
    # early-complete with it if the terminal status lags.
    partial: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    # Hint to the UI for the next poll. The UI is free to ignore it
    # and use its own backoff schedule, but this lets the server
    # tighten / loosen the cadence based on actual Genie behavior.
    attempt_after_ms: Optional[int] = 3000


@router.post("/genie", response_model=GeniePollResponse)
async def poll_genie(
    body: GeniePollRequest,
    req: Request,
    current_user: User = Depends(get_current_user),
) -> GeniePollResponse:
    """Poll a single Genie query for completion.

    Prefers the user's OBO token (forwarded by Databricks Apps). When
    that's not available (local dev, non-Apps deployments), falls back
    to a service principal OAuth token via :func:`call_genie_tool`.
    The kickoff call already established the auth source, so we use the
    same path here for symmetry.
    """
    obo_token: Optional[str] = None
    if hasattr(req, "state") and hasattr(req.state, "token"):
        obo_token = req.state.token

    # Probe for streaming/progress support: log any progress notifications the
    # Genie MCP server emits during the poll. If these never appear, Genie does
    # not stream over MCP and we must keep relying on discrete polls.
    async def _on_progress(
        progress: float, total: Optional[float], message: Optional[str]
    ) -> None:
        logger.info(
            "Genie poll progress [conv=%s]: progress=%s total=%s message=%s",
            body.conversation_id,
            progress,
            total,
            message,
        )

    try:
        response = await call_genie_tool(
            tool_name="genie_poll_response",
            arguments={
                "conversation_id": body.conversation_id,
                # Managed-MCP Genie expects the per-question handle as
                # ``response_id`` (matching what ``genie_ask`` returned).
                # Internally we call it ``message_id`` for protocol
                # consistency; we translate at this boundary.
                "response_id": body.message_id,
            },
            obo_token=obo_token,
            space_id=body.space_id or None,
            progress_callback=_on_progress,
        )
    except GenieAuthUnavailableError as e:
        # No auth available at all (no OBO header, no SP fallback). 401
        # is more useful than a generic "failed" because the UI can
        # surface the auth message verbatim.
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(
            "Genie poll failed for user %s on space %s: %s",
            current_user.email,
            body.space_id or "<general>",
            e,
            exc_info=True,
        )
        return GeniePollResponse(status="failed", error=f"Poll failed: {e}")

    if response.get("is_error"):
        return GeniePollResponse(
            status="failed",
            error=response.get("content") or "Genie reported an error.",
        )

    parsed = _parse_genie_response(response, body)
    return parsed


_URL_FIELD_NAMES = (
    "conversation_url",
    "share_url",
    "share_link",
    "deep_link",
    "deeplink",
    "permalink",
    "link",
    "url",
)


def _find_genie_deep_link_in_payload(payload: Dict[str, Any]) -> Optional[str]:
    """Scan a Genie response payload for a Databricks-supplied URL.

    The MCP managed-Genie docs promise "a deep link back to the
    conversation in the Databricks UI" but don't pin down the field
    name, and the shape evolves between releases. We walk the top
    level (and one level into ``attachments``) checking a handful of
    likely names and return the first ``http(s)://...databricks...``
    URL we encounter.

    Whatever Genie hands us is treated as authoritative — earlier
    versions of this code tried to filter out URLs that "looked like"
    workspace home pages, but that rejected legitimate per-conversation
    URLs in customer environments and produced a "no link" UX even
    when Genie was supplying a real one. The rule now is simple: if
    the field exists, points at a Databricks workspace, and isn't a
    placeholder, render it.
    """

    def _looks_like_databricks_url(value: object) -> Optional[str]:
        if not isinstance(value, str):
            return None
        v = value.strip()
        if not v:
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            return None
        # Only accept Databricks-hosted URLs to avoid e.g. an upstream
        # bug putting a tracking URL in the same field.
        if "databricks." not in v:
            return None
        return v

    candidates: list[Any] = []
    candidates.extend(payload.get(k) for k in _URL_FIELD_NAMES)

    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        for att in attachments:
            if isinstance(att, dict):
                candidates.extend(att.get(k) for k in _URL_FIELD_NAMES)
                # Some shapes nest URLs inside ``query`` or ``share``.
                for nest_key in ("query", "share", "metadata"):
                    nested = att.get(nest_key)
                    if isinstance(nested, dict):
                        candidates.extend(nested.get(k) for k in _URL_FIELD_NAMES)

    for c in candidates:
        url = _looks_like_databricks_url(c)
        if url:
            return url
    return None


_STEP_TEXT_KEYS = (
    "content",
    "text",
    "description",
    "message",
    "markdown",
    "body",
    "summary",
    "detail",
    "details",
    "title",
    "name",
    "label",
    "step",
)


def _extract_step_text(step: Any) -> str:
    """Best-effort pull of the human-readable text from one progress step.

    Genie's progress-step schema isn't stable across versions, so we try a set
    of known text keys first, then fall back to the longest string value in the
    dict. Returns an empty string when nothing usable is found.
    """
    if isinstance(step, str):
        return step.strip()
    if not isinstance(step, dict):
        return ""
    for key in _STEP_TEXT_KEYS:
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Fallback: the longest string value anywhere in the dict.
    longest = ""
    for val in step.values():
        if isinstance(val, str) and len(val.strip()) > len(longest):
            longest = val.strip()
    return longest


# Genie embeds raw query-result tables between HTML comment markers like
# ``<!-- begin:query_abc123 -->...<!-- end:query_abc123 -->``. They're huge and
# unreadable in a live progress feed, so we strip them out entirely.
_QUERY_BLOCK_RE = re.compile(r"<!--\s*begin:.*?-->.*?<!--\s*end:.*?-->", re.DOTALL)
# How many of the most recent steps to surface — a rolling window keeps the
# progress feed compact and "live" instead of an ever-growing wall of text.
_NARRATION_WINDOW = 6
# Cap any single step (e.g. a long SQL statement) so it reads as a hint, not a
# code dump.
_STEP_MAX_LEN = 160


def _clean_step_text(text: str) -> str:
    """Turn a raw progress step into a short, readable one-liner.

    Strips embedded query-result tables, collapses whitespace, and truncates
    long statements (SQL) so the live feed shows intent ("Running SQL…",
    "Query returned N rows") rather than raw data dumps.
    """
    text = _QUERY_BLOCK_RE.sub("", text)
    # Drop any leftover markdown table rows (lines starting with a pipe).
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("|")]
    cleaned = " ".join(" ".join(lines).split())
    # Trim a trailing "label:" left behind after stripping a result table.
    cleaned = cleaned.rstrip(": ").strip()
    if len(cleaned) > _STEP_MAX_LEN:
        cleaned = cleaned[:_STEP_MAX_LEN].rstrip() + "…"
    return cleaned


def _build_stream_narration(steps: Any) -> str:
    """Assemble a compact, readable narration from Genie's ``progress_steps``.

    Shows the most recent steps (a rolling window) with raw result tables and
    overly long SQL stripped/truncated. The list can revise/shrink between
    polls, so the UI renders this by replacing the snapshot each tick.
    """
    if not isinstance(steps, list) or not steps:
        return ""
    lines: List[str] = []
    for step in steps:
        text = _clean_step_text(_extract_step_text(step))
        if text:
            lines.append(text)
    if not lines:
        return ""
    return "\n".join(lines[-_NARRATION_WINDOW:]).strip()


def _summarize_genie_payload(
    response: Dict[str, Any], payload: Optional[Dict[str, Any]]
) -> str:
    """Compact, log-safe description of a Genie poll response shape.

    Used to debug completion-detection gaps: it surfaces the raw status, the
    payload's top-level keys, content length, and attachment shape without
    dumping full (potentially large / sensitive) answer rows at INFO level.
    """
    content = response.get("content")
    content_len = len(content) if isinstance(content, str) else 0
    top_keys = sorted(k for k in payload.keys() if isinstance(k, str)) if isinstance(payload, dict) else []
    raw_status = ""
    nested_status = ""
    attachments_summary = "none"
    # Which field actually carries the streamed answer? Genie may stream
    # narration via ``progress_steps`` while leaving ``final_answer`` empty
    # until the end — this surfaces that so we can stream the right field.
    stream_shape = ""
    if isinstance(payload, dict):
        fa = payload.get("final_answer")
        fa_len = len(fa) if isinstance(fa, str) else 0
        steps = payload.get("progress_steps")
        steps_len = len(steps) if isinstance(steps, list) else 0
        items = payload.get("query_items")
        items_len = len(items) if isinstance(items, list) else 0
        # Counts only (no step text) — enough to see streaming progress without
        # echoing potentially sensitive data content into the logs.
        stream_shape = (
            f"final_answer_len={fa_len} progress_steps={steps_len} "
            f"query_items={items_len}"
        )
    if isinstance(payload, dict):
        raw_status = str(payload.get("status") or payload.get("state") or "")
        # Look one level deeper — some MCP shapes nest the status under a
        # ``message`` (or similar) object rather than at the top level.
        for nest_key in ("message", "result", "data"):
            nested = payload.get(nest_key)
            if isinstance(nested, dict):
                ns = nested.get("status") or nested.get("state")
                if ns:
                    nested_status = f"{nest_key}.status={ns}"
                    break
        atts = payload.get("attachments")
        if isinstance(atts, list) and atts:
            first = atts[0] if isinstance(atts[0], dict) else None
            first_keys = sorted(k for k in first.keys() if isinstance(k, str)) if first else []
            attachments_summary = f"{len(atts)} (first keys={first_keys})"
    return (
        f"raw_status={raw_status or '∅'} {nested_status} "
        f"top_keys={top_keys} content_len={content_len} attachments={attachments_summary} "
        f"{stream_shape}"
    ).strip()


def _parse_genie_response(
    response: Dict[str, Any],
    body: GeniePollRequest,
) -> GeniePollResponse:
    """Translate a Genie MCP response into our poll wire shape.

    Genie's poll responses are not 100% standardized across versions;
    we look for a status field in either the structured payload or
    a JSON-decoded text body, and treat anything we don't recognize
    as still running so the UI keeps polling rather than failing
    fast on a transient parse mismatch.

    On completion we also enrich the payload with a per-conversation
    Databricks deep link so the UI can render an "Open in Databricks
    Genie" CTA without having to reconstruct the URL from
    ``DATABRICKS_HOST`` itself.
    """
    structured = response.get("structured")
    payload: Optional[Dict[str, Any]] = None
    if isinstance(structured, dict):
        payload = structured
    else:
        text = response.get("content")
        if isinstance(text, str) and text.strip():
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                payload = decoded

    raw_status = ""
    if isinstance(payload, dict):
        raw_status = str(payload.get("status") or payload.get("state") or "").upper()

    if raw_status in ("COMPLETED", "SUCCESS", "DONE"):
        result = _enrich_result(payload, response, body)
        _log_terminal_poll(result, response, terminal=True)
        return GeniePollResponse(
            status="complete",
            result=result,
            attempt_after_ms=None,
        )
    if raw_status in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
        err: Optional[str] = None
        if isinstance(payload, dict):
            err = (
                payload.get("error")
                or payload.get("error_message")
                or payload.get("status_message")
            )
        return GeniePollResponse(
            status="failed",
            error=err or response.get("content") or "Genie query failed.",
            attempt_after_ms=None,
        )

    # Still running. Genie streams the answer by re-sending the full
    # ``final_answer`` snapshot every poll while ``status`` stays
    # ``in_progress`` (the terminal flip can lag well past when the answer is
    # actually ready). We surface that snapshot as ``partial`` so the UI can
    # render it live (replacing, since it can change non-additively) and, if the
    # terminal status lags, early-complete with it. This is normal behavior —
    # not an error — so we log it at INFO/DEBUG.
    partial = _enrich_result(payload, response, body) if isinstance(payload, dict) else None
    logger.info(
        "Genie poll still running: %s", _summarize_genie_payload(response, payload)
    )
    return GeniePollResponse(status="running", partial=partial, attempt_after_ms=3000)


def _enrich_result(
    payload: Optional[Dict[str, Any]],
    response: Dict[str, Any],
    body: GeniePollRequest,
) -> Dict[str, Any]:
    """Build the enriched result/partial dict from a Genie poll payload.

    Shared by the terminal ``complete`` branch and the in-progress ``partial``
    branch so a streamed snapshot and the final answer have identical shape
    (conversation id, deep link, auth/space hints). Underscore-prefixed keys are
    ours and won't clash with Genie's own fields.
    """
    result: Dict[str, Any] = dict(payload) if payload else {"text": response.get("content")}
    conversation_id = (
        result.get("conversation_id")
        or result.get("conversationId")
        or body.conversation_id
    )
    if conversation_id and "conversation_id" not in result:
        result["conversation_id"] = conversation_id
    # Genie streams its work as ``progress_steps`` while ``final_answer`` stays
    # empty until the very end. Surface a normalized narration string the UI can
    # render live so the user sees progress instead of a static spinner. (Once
    # ``final_answer`` lands at completion the UI prefers that.)
    narration = _build_stream_narration(result.get("progress_steps"))
    if narration:
        result["_stream_narration"] = narration
    # Surface a deep link only when Genie supplied one AND the call ran under
    # the user's own identity. Under the local-dev SP fallback the conversation
    # is SP-owned, so the link would 404 in the user's Databricks One history.
    deep_link: Optional[str] = None
    auth_source = response.get("auth_source")
    if auth_source == "obo":
        deep_link = _find_genie_deep_link_in_payload(result)
    if deep_link:
        result["_deep_link"] = deep_link
    if auth_source:
        result.setdefault("_auth_source", auth_source)
    if body.space_id:
        result.setdefault("_space_id", body.space_id)
    return result


def _log_terminal_poll(
    result: Dict[str, Any], response: Dict[str, Any], terminal: bool
) -> None:
    """Log a compact summary of a completed Genie poll (keys, deep link, etc.)."""
    try:
        auth_source = response.get("auth_source")
        top_keys = sorted(k for k in result.keys() if isinstance(k, str))
        atts = result.get("attachments")
        attachment_summary = "none"
        if isinstance(atts, list) and atts and isinstance(atts[0], dict):
            attachment_summary = (
                f"{len(atts)} attachment(s); first keys="
                f"{sorted(k for k in atts[0].keys() if isinstance(k, str))}"
            )
        url_field_snapshot: dict[str, str] = {}
        for k in _URL_FIELD_NAMES:
            v = result.get(k)
            if v is not None:
                s = repr(v)
                url_field_snapshot[k] = s[:200] + ("…" if len(s) > 200 else "")
        logger.info(
            "Genie poll complete: auth=%s top_keys=%s attachments=%s "
            "url_fields=%s deep_link_found=%s",
            auth_source or "unknown",
            top_keys,
            attachment_summary,
            url_field_snapshot or "none",
            "_deep_link" in result,
        )
        logger.debug("Genie poll full payload: %s", json.dumps(result, default=str))
    except Exception:  # noqa: BLE001 — logging must never break the response
        pass
