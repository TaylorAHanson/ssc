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
from typing import Any, Dict, Literal, Optional

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
        # Best-effort enrichment. Keys are namespaced with a single
        # underscore prefix so they don't clash with Genie's own
        # response fields if the upstream schema gains the same name.
        result: Dict[str, Any] = dict(payload) if payload else {"text": response.get("content")}
        conversation_id = (
            result.get("conversation_id")
            or result.get("conversationId")
            or body.conversation_id
        )
        if conversation_id and "conversation_id" not in result:
            result["conversation_id"] = conversation_id
        # Surface a deep link only when Genie itself supplied one AND
        # the call ran under the user's own identity. When we fall
        # back to the service principal in local dev, the conversation
        # is owned by the SP — clicking the link would land the user
        # on Databricks One's "Conversation not found" page because
        # their personal Genie chat history doesn't include SP-owned
        # threads. The link works fine in deployed environments where
        # OBO is mandatory.
        deep_link: Optional[str] = None
        auth_source = response.get("auth_source")
        if isinstance(result, dict) and auth_source == "obo":
            deep_link = _find_genie_deep_link_in_payload(result)
        if deep_link:
            result["_deep_link"] = deep_link
        # Always echo the auth mode so the UI can render a small
        # local-dev hint instead of leaving the panel feeling broken.
        if auth_source:
            result.setdefault("_auth_source", auth_source)
        # Echo the space scope back so the UI can label cards
        # appropriately (and disambiguate when one chat session has
        # mixed general + space-scoped Genie calls).
        if body.space_id:
            result.setdefault("_space_id", body.space_id)
        # Visibility: log the top-level keys, the actual deep_link
        # value (truncated), and a sample of attachment keys so we can
        # see what Genie returned without spamming the log with full
        # payloads. Use DEBUG for the full dump.
        try:
            top_keys = sorted(k for k in result.keys() if isinstance(k, str))
            attachment_summary: Optional[str] = None
            atts = result.get("attachments")
            if isinstance(atts, list) and atts:
                first = atts[0] if isinstance(atts[0], dict) else None
                if first is not None:
                    attachment_summary = (
                        f"{len(atts)} attachment(s); first keys="
                        f"{sorted(k for k in first.keys() if isinstance(k, str))}"
                    )
            # Snapshot whatever value(s) Genie put in URL-shaped fields
            # so we can debug "deep_link_found=false but a key is
            # there" cases. Truncate to 200 chars to be safe.
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
                attachment_summary or "none",
                url_field_snapshot or "none",
                bool(deep_link),
            )
            logger.debug("Genie poll full payload: %s", json.dumps(result, default=str))
        except Exception:  # noqa: BLE001 — logging must never break the response
            pass
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
    # Default: still running. The UI uses attempt_after_ms to schedule
    # the next poll.
    return GeniePollResponse(status="running", attempt_after_ms=3000)
