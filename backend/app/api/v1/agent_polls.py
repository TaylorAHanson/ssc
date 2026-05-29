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

    parsed = _parse_genie_response(response)
    return parsed


def _parse_genie_response(response: Dict[str, Any]) -> GeniePollResponse:
    """Translate a Genie MCP response into our poll wire shape.

    Genie's poll responses are not 100% standardized across versions;
    we look for a status field in either the structured payload or
    a JSON-decoded text body, and treat anything we don't recognize
    as still running so the UI keeps polling rather than failing
    fast on a transient parse mismatch.
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
        return GeniePollResponse(
            status="complete",
            result=payload or {"text": response.get("content")},
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
