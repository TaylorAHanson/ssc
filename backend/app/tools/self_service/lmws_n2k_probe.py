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
    SYSTEM_ENDPOINTS,
    LmwsN2kProbeClient,
)
from app.tools.mcp import tool

logger = logging.getLogger(__name__)


class LmwsProbeConfigInput(BaseModel):
    refresh: bool = Field(
        default=False,
        description=(
            "Re-read the LMWS service-account password instead of using the cached "
            "value. Set true right after granting the app's service principal READ on "
            "the secret scope: failed secret reads are cached for the life of the "
            "process, so the app would otherwise keep reporting the old failure until "
            "it restarts. Only the LMWS credential is refreshed."
        ),
    )


@tool(
    name="lmws_probe_config",
    description="Platform-admin diagnostic: verify LMWS configuration, secret scope access, and endpoint URLs without calling the gateway.",
    args_schema=LmwsProbeConfigInput,
    side_effect_class="read",
    required_role="platform_admin",
    feature_flag="core",
    friendly_label="Checking LMWS configuration...",
)
async def lmws_probe_config(refresh: bool = False, **kwargs) -> Dict[str, Any]:
    result = await LmwsN2kProbeClient().probe_config(refresh=refresh)
    logger.info(
        "lmws_probe_config: refresh=%s ready=%s password_resolved=%s missing_urls=%s",
        refresh, result.get("ready"), result.get("password_resolved"),
        result.get("missing_base_urls"),
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
    description="Platform-admin diagnostic: call a read-only LMWS endpoint (e.g. n2kListMetadataGet, requestStatus) using service account credentials.",
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
    system_endpoint: str = Field(
        default="auto",
        description=(
            "FWS 'addMembers' only; ignored by the LMWS endpoints. Which directory "
            "backs the list: 'ActiveDirectory' for traditional ListManager lists, "
            "'Azure' for Saviynt-created ones (names starting Sav_Azure_ / Sav_Auto_). "
            "'auto' (default) guesses from the list name and, on a 404, retries once "
            "with the other value — a 404 means the list was not found in the "
            "directory tried, not that it does not exist."
        ),
    )
    requester: Optional[str] = Field(
        default=None,
        description=(
            "FWS 'addMembers' only; ignored by the LMWS endpoints. The CN this add is "
            "made ON BEHALF OF, sent alongside actor (the service account). This is "
            "the delegation field no LMWS add endpoint has, so it is the one lever "
            "that may work when the service account is not a list supervisor — set it "
            "to a user who IS entitled on the list. Blank sends the service account "
            "as its own requester."
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
    description="Platform-admin diagnostic: probe LMWS and FWS membership-add endpoints for an identity list to test ACL and supervisor status. Defaults to dry_run=True.",
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
    system_endpoint: str = "auto",
    requester: Optional[str] = None,
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
    if system_endpoint != "auto" and system_endpoint not in SYSTEM_ENDPOINTS:
        return {
            "status": "error",
            "detail": (
                f"system_endpoint must be 'auto' or one of {', '.join(SYSTEM_ENDPOINTS)}."
            ),
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
            system_endpoint=system_endpoint,
            requester=requester,
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
        system_endpoint=system_endpoint,
        requester=requester,
        base_url=base_url,
        dry_run=dry_run,
        timeout_seconds=timeout_seconds,
    )
    logger.info(
        "lmws_probe_membership_add matrix: list=%s dry_run=%s succeeded=%s",
        list_name, dry_run, matrix.get("endpoints_succeeded"),
    )
    return matrix
