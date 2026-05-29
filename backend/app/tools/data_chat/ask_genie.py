"""
``ask_your_data`` tool: Databricks Genie via Managed MCP.

Databricks Genie (the general-purpose chat in Databricks One) answers
natural-language questions about enterprise data. By default this tool
calls the ``/api/2.0/mcp/genie`` Managed MCP server, which searches
across the caller's accessible Unity Catalog data *and* any Genie
Spaces they're entitled to. An optional ``space_id`` pins the call to
a single curated Genie Space (``/api/2.0/mcp/genie/{space_id}``) when
that's desired.

The server is *asynchronous*: ``genie_ask`` starts the query, then we
poll ``genie_poll_response`` to drain the answer. This tool only does
the start step: it returns a "pending poll" envelope that the agent
runner surfaces to the UI as a :class:`PendingPollEvent`. The UI polls
a separate endpoint (``/agent/poll/genie``) to drive the answer to
completion.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.providers.databricks_mcp import GenieAuthUnavailableError, build_genie_mcp_url, call_genie_tool
from app.tools.mcp import tool

logger = logging.getLogger(__name__)


_DESCRIPTION = """\
Ask a natural-language question to Databricks Genie - the general-purpose data chat \
inside Databricks. Genie searches across the caller's accessible Unity Catalog data \
and any Genie Spaces they're entitled to, then returns a grounded answer with a \
deep link back to the conversation in Databricks. This is a slow tool (typically \
30-120 seconds). Use it when you need actual data, not platform metadata.

Use it for:
- Counts, trends, aggregations across business data ("how many active customers last quarter?")
- Joins / lookups across tables ("what's the average order value by region?")
- Open-ended questions that require querying actual rows of data.
- Discovery questions like "what data is available?" or "what tables can you query?" - \
Genie is grounded in the caller's accessible UC data and is the right place to ask.

Prefer faster tools for:
- Listing UC catalogs/schemas/tables structurally => use get_table_list / get_catalog_list when available.
- User entitlements / who has access to what => use search_user_entitlements.
- Platform metadata (workspaces, jobs, audit logs) => use the dedicated tools.
- Questions answerable from your own knowledge / system prompt context.

Calling this tool returns immediately with a pending-poll handle; the user sees \
"Asking Genie..." and the answer streams in once it's ready.\
"""


class AskYourDataInput(BaseModel):
    """Schema for the ``ask_your_data`` tool."""

    question: str = Field(
        ...,
        min_length=4,
        description=(
            "The literal natural-language question to forward to Genie. "
            "Pass the user's question verbatim where possible; do not "
            "reformulate into SQL."
        ),
    )
    space_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional Genie Space ID. Leave empty to use general "
            "Databricks Genie (the default), which searches across "
            "the caller's accessible Unity Catalog data and any "
            "spaces they have. Set this only to pin the call to a "
            "specific curated Genie Space."
        ),
    )


@tool(
    name="ask_your_data",
    description=_DESCRIPTION,
    args_schema=AskYourDataInput,
    feature_flag="ask_your_data",
    friendly_label="Asking Genie...",
    friendly_completion_label="Genie answered.",
)
async def ask_your_data(
    question: str,
    space_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Start a Genie query and return a pending-poll envelope.

    The agent runner's pending-poll detector sees the
    ``{"pending_poll": {...}}`` shape, halts iteration, and emits a
    :class:`~app.agents.events.PendingPollEvent` so the UI can drain
    the answer via the ``/agent/poll/genie`` endpoint.
    """
    obo_token: Optional[str] = kwargs.get("_obo_token")

    # space_id is optional. None => general Databricks Genie (searches
    # across all of the caller's accessible UC data + spaces). A value =>
    # space-scoped Genie. We honor an optional default from configuration
    # for organizations that want to pin to one curated space, but it is
    # NOT required.
    space = space_id or _resolved_default_space_id()

    try:
        # Validate the URL eagerly so configuration mistakes (bad host)
        # surface as a tool error to the user instead of crashing the
        # runner.
        build_genie_mcp_url(space_id=space)
    except ValueError as e:
        return {"error": str(e)}

    try:
        # call_genie_tool resolves auth: prefers OBO, falls back to SP
        # OAuth via the SDK in environments without the
        # X-Forwarded-Access-Token header (local dev). In Databricks
        # Apps the OBO token is always present so the fallback never
        # triggers.
        response = await call_genie_tool(
            tool_name="genie_ask",
            arguments={"question": question},
            obo_token=obo_token,
            space_id=space,
        )
    except GenieAuthUnavailableError as e:
        # No auth available at all (no OBO and SP fallback failed).
        return {"error": str(e)}
    except Exception as e:  # network, MCP protocol errors
        logger.error("Genie genie_ask failed: %s", e, exc_info=True)
        return {"error": f"Failed to contact Genie: {e}"}

    if response.get("is_error"):
        return {
            "error": (
                response.get("content")
                or "Genie reported an error starting the query."
            )
        }

    handle = _extract_query_handle(response)
    if not handle:
        return {
            "error": (
                "Genie did not return a query handle. Raw response: "
                + (response.get("content") or "<empty>")
            )
        }

    return {
        "pending_poll": {
            "kind": "genie",
            "friendly_label": "Asking Genie...",
            # space_id is optional in the envelope. Carrying it through
            # lets the poll endpoint reuse the same scoping; absent =>
            # general Genie.
            "space_id": space,
            "conversation_id": handle.get("conversation_id"),
            "message_id": handle.get("message_id"),
            "question": question,
            # auth_source lets the UI / poll endpoint know whether this
            # was OBO ("obo") or the SP fallback ("sp"). The fallback is
            # only expected in local dev.
            "auth_source": response.get("auth_source"),
        }
    }


def _resolved_default_space_id() -> Optional[str]:
    """Look up an optional default Genie Space.

    Lives in ``configuration.yaml`` under
    ``tools.ask_your_data.default_genie_space_id``. This is **optional**:
    leaving it empty (or absent) is the normal case and tells the tool to
    use the general Databricks Genie endpoint. Set it only when you want
    to pin all Ask Your Data traffic to a single curated Genie Space.
    """
    try:
        from app.core.feature_flags import _yaml_config  # type: ignore[attr-defined]
    except Exception:
        return None
    tools_cfg = _yaml_config.get("tools") or {}
    entry = tools_cfg.get("ask_your_data")
    if isinstance(entry, dict):
        return entry.get("default_genie_space_id") or None
    return None


def _extract_query_handle(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull conversation_id / message_id out of the genie_ask response.

    The MCP transport returns the structured part as a dict, so we
    prefer that. As a fallback we try to JSON-decode the text content
    in case the server only ships text frames.
    """
    structured = response.get("structured")
    if isinstance(structured, dict):
        return _normalize_handle(structured)
    text = response.get("content")
    if isinstance(text, str) and text.strip():
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, dict):
            return _normalize_handle(decoded)
    return None


def _normalize_handle(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Accept a few common key spellings the upstream API might emit.

    Notably the Managed-MCP Genie server returns the per-question handle
    as ``response_id`` (sibling of ``conversation_id``); the older REST
    Genie API used ``message_id``. We accept either and normalize to
    ``message_id`` for the rest of the codebase. The poll endpoint then
    passes the value back to Genie under the name it actually expects
    (``response_id``).
    """
    conversation_id = (
        payload.get("conversation_id")
        or payload.get("conversationId")
        or payload.get("conversation")
    )
    message_id = (
        payload.get("response_id")
        or payload.get("responseId")
        or payload.get("message_id")
        or payload.get("messageId")
        or payload.get("query_id")
        or payload.get("id")
    )
    if not conversation_id or not message_id:
        return None
    return {"conversation_id": conversation_id, "message_id": message_id}


__all__ = ["ask_your_data"]
