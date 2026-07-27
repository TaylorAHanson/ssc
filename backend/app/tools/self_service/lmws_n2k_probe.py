"""Admin-only tools for probing the N2K-aware LMWS membership endpoints.

Diagnostics for the "Requester is not authorized to view or modify N2K list"
failure: the production ``add_group_membership`` path calls ``listMembersAdd``,
which refuses need-to-know lists outright, while LMWS exposes separate
``n2k*`` / ``listAnyMembershipAdd`` endpoints for them. These tools establish
which of those the service account can actually use for a given list, before any
of it is wired into the governed membership path.

They are Platform-Admin-gated and intentionally *not* alternatives to
``add_group_membership`` — the write probe defaults to ``dry_run=True`` and
exists to characterize the endpoints, not to service user requests. See
:mod:`app.providers.lmws.n2k` for the endpoint tables and response envelope.
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.providers.lmws.n2k import (
    ADD_ENDPOINTS,
    MEMBER_STYLES,
    READ_ENDPOINTS,
    LmwsN2kProbeClient,
)
from app.tools.mcp import tool

logger = logging.getLogger(__name__)


class LmwsProbeConfigInput(BaseModel):
    pass


@tool(
    name="lmws_probe_config",
    description=(
        "Platform-admin diagnostic: report whether LMWS is CONFIGURED in this "
        "environment, without contacting the gateway. Call this FIRST whenever any "
        "LMWS operation fails with a 'not configured' or credential error — it says "
        "which of the three independent causes applies: the app's service principal "
        "cannot read the secret scope (a Databricks grant), the scope is readable but "
        "the password key is missing (an IAM request), or a base URL is blank (a "
        "deployment/settings change). Also reports the service account and which base "
        "URLs are set. Never returns the password itself."
    ),
    args_schema=LmwsProbeConfigInput,
    side_effect_class="read",
    required_role="platform_admin",
    feature_flag="core",
    friendly_label="Checking LMWS configuration...",
)
async def lmws_probe_config(**kwargs) -> Dict[str, Any]:
    result = await LmwsN2kProbeClient().probe_config()
    logger.info(
        "lmws_probe_config: ready=%s password_resolved=%s missing_urls=%s",
        result.get("ready"), result.get("password_resolved"), result.get("missing_base_urls"),
    )
    return result


class LmwsProbeReadInput(BaseModel):
    endpoint: str = Field(
        ...,
        description=(
            "Read-only LMWS endpoint to call. One of: "
            + ", ".join(sorted(READ_ENDPOINTS))
            + ". 'n2kListMetadataGet' is the one to start with — it reports whether a "
            "list is N2K and whether it requires a JQS justification form. "
            "'requestStatus' follows up on a request id returned by an N2K add."
        ),
    )
    params: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Query parameters as a flat object, e.g. {\"listName\": \"edh_dbx_enterprise_deng\"}. "
            "For n2kListMetadataGet use listName. For requestStatus use requestid and/or "
            "reqkey (both lowercase; reqkey wins if both are given). Passed through "
            "verbatim, so an unconfirmed parameter name can also be tested."
        ),
    )
    interpret: bool = Field(
        default=True,
        description=(
            "Parse the response into a direct answer where the shape is known: for "
            "n2kListMetadataGet, flatten the nested metadataInfos array and report "
            "is_n2k / requires_jqs / jqs_form; for requestStatus, surface the status "
            "and approvers. Set false to get only the raw body."
        ),
    )
    base_url: Optional[str] = Field(
        default=None,
        description=(
            "Override the configured base URL to target a specific environment, e.g. "
            "https://dev.apigw-op.example.com/iam/v1/lmws-rest/publicAPIrest. Blank uses "
            "the configured LMWS URL for this endpoint."
        ),
    )
    timeout_seconds: Optional[float] = Field(
        default=None, ge=2, le=120,
        description="Per-request timeout. Blank uses the configured native-LMWS timeout.",
    )


@tool(
    name="lmws_probe_read",
    description=(
        "Platform-admin diagnostic: call a READ-ONLY LMWS endpoint with the app's "
        "service account and report exactly what the gateway returned (HTTP status, "
        "body-level errors, latency) without raising. Start here before any N2K "
        "membership work: n2kListMetadataGet with {\"listName\": \"<list>\"} says "
        "whether the list is N2K and whether it requires a JQS justification form — "
        "if it does, adds cannot be automated because the JQS answer id is only "
        "obtainable through a browser form. Use requestStatus with {\"requestid\": "
        "\"<id>\"} to follow a request produced by an add. This changes nothing. For "
        "ordinary group questions use member_lookup or group_lookup instead; this "
        "tool is for characterizing the LMWS API itself."
    ),
    args_schema=LmwsProbeReadInput,
    side_effect_class="read",
    required_role="platform_admin",
    feature_flag="core",
    friendly_label="Probing LMWS endpoint...",
)
async def lmws_probe_read(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    interpret: bool = True,
    base_url: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    **kwargs,
) -> Dict[str, Any]:
    client = LmwsN2kProbeClient()
    params = params or {}

    # Route the two endpoints with a known response shape through their parsing
    # helpers so the answer comes back interpreted rather than deeply nested.
    if interpret and endpoint == "n2kListMetadataGet" and params.get("listName"):
        result = await client.probe_list_metadata(
            str(params["listName"]), base_url=base_url, timeout_seconds=timeout_seconds
        )
    elif interpret and endpoint == "requestStatus" and (
        params.get("requestid") or params.get("reqkey") or params.get("requestId")
    ):
        result = await client.probe_request_status(
            requestid=params.get("requestid") or params.get("requestId"),
            reqkey=params.get("reqkey") or params.get("reqKey"),
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
    else:
        result = await client.probe_read(
            endpoint, params, base_url=base_url, timeout_seconds=timeout_seconds
        )

    logger.info(
        "lmws_probe_read: endpoint=%s outcome=%s http=%s",
        endpoint, result.get("outcome"), result.get("http_status"),
    )
    return result


class LmwsProbeAddInput(BaseModel):
    list_name: str = Field(..., description="Target LMWS list name, e.g. edh_dbx_enterprise_deng.")
    members: List[str] = Field(
        ...,
        description="Corporate usernames (CNs, not email addresses) to add.",
    )
    endpoint: Optional[str] = Field(
        default=None,
        description=(
            "Which membership-add endpoint to probe: "
            + ", ".join(sorted(ADD_ENDPOINTS))
            + ". Blank probes ALL of them and returns a comparison matrix, which is "
            "usually what you want on a first run. 'listAnyMembershipAdd' routes "
            "internally for N2K/non-N2K lists and needs only the general REST ACL, so "
            "it is the most likely to succeed; 'listMembersAdd' is the current "
            "production endpoint and is expected to reject N2K lists."
        ),
    )
    justification: Optional[str] = Field(
        default=None,
        description=(
            "Justification text. If the list uses a JQS justification form (check "
            "n2kListMetadataGet first), this must instead be a JQS answer id in the "
            "form [[answerId]], which only a browser form can produce. Blank uses the "
            "configured default."
        ),
    )
    member_style: str = Field(
        default="auto",
        description=(
            "How to encode the listMembers parameter. 'auto' (default) matches the "
            "documented examples: a bare username for one member, [u1,u2] for several. "
            "'bracketed' always brackets, 'csv' sends u1,u2 (what the production path "
            "does), 'repeated' sends listMembers=u1&listMembers=u2. Vary this only if "
            "an endpoint rejects the members argument itself."
        ),
    )
    dry_run: bool = Field(
        default=True,
        description=(
            "True (default) builds the request and returns the exact URL and parameters "
            "WITHOUT sending it. Set false to actually call the gateway — that adds "
            "real members and may file an approval request."
        ),
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Override the configured base URL to target a specific environment.",
    )
    timeout_seconds: Optional[float] = Field(
        default=None, ge=2, le=120,
        description="Per-request timeout. Blank uses the configured native-LMWS timeout.",
    )


@tool(
    name="lmws_probe_membership_add",
    description=(
        "Platform-admin diagnostic: probe the LMWS membership-ADD endpoints for a list "
        "to determine which ones the service account can use — specifically for N2K "
        "(need-to-know) lists that the production listMembersAdd endpoint refuses. "
        "Leave 'endpoint' blank to compare all candidates in one run. The outcome "
        "distinguishes the two failure modes that need opposite fixes: 'acl_missing' "
        "means the service account lacks the lmws.rest / listmanager-n2k-admins ACL, "
        "while 'supervisor_required' means the ACL passed but the account is not a "
        "supervisor of that specific list (which must be granted per-list by the list "
        "owner — no API exists for it). Defaults to dry_run=true, returning the exact "
        "request without calling the gateway; dry_run=false performs a REAL membership "
        "change and may file an approval request, so only do that when explicitly "
        "asked. This is a diagnostic — route genuine access requests through the "
        "governed add_group_membership path instead."
    ),
    args_schema=LmwsProbeAddInput,
    side_effect_class="membership",
    required_role="platform_admin",
    feature_flag="core",
    friendly_label="Probing LMWS membership endpoints...",
)
async def lmws_probe_membership_add(
    list_name: str,
    members: List[str],
    endpoint: Optional[str] = None,
    justification: Optional[str] = None,
    member_style: str = "auto",
    dry_run: bool = True,
    base_url: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    **kwargs,
) -> Dict[str, Any]:
    if member_style not in MEMBER_STYLES:
        return {
            "status": "error",
            "detail": f"member_style must be one of {', '.join(MEMBER_STYLES)}.",
        }

    # Match the normalization the production membership path applies, so a probe
    # result is a valid prediction of what add_group_membership would send.
    from app.tools.self_service.identity_groups import _normalize_member

    normalized = [m for m in (_normalize_member(m) for m in members) if m]
    if not normalized:
        return {"status": "error", "detail": "No valid members after normalization."}
    if normalized != members:
        logger.info(
            "lmws_probe_membership_add: normalized members %r -> %r", members, normalized
        )

    client = LmwsN2kProbeClient()
    if endpoint:
        result = await client.probe_membership_add(
            endpoint,
            list_name,
            normalized,
            justification=justification,
            member_style=member_style,
            base_url=base_url,
            dry_run=dry_run,
            timeout_seconds=timeout_seconds,
        )
        logger.info(
            "lmws_probe_membership_add: endpoint=%s list=%s dry_run=%s outcome=%s",
            endpoint, list_name, dry_run, result.get("outcome"),
        )
        return result

    matrix = await client.probe_add_matrix(
        list_name,
        normalized,
        justification=justification,
        member_style=member_style,
        base_url=base_url,
        dry_run=dry_run,
        timeout_seconds=timeout_seconds,
    )
    logger.info(
        "lmws_probe_membership_add matrix: list=%s dry_run=%s succeeded=%s",
        list_name, dry_run, matrix.get("endpoints_succeeded"),
    )
    return matrix
