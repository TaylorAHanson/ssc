"""
Tool: ping target Databricks workspace(s) to prove SDK reachability.

This is a connectivity diagnostic. From wherever this app runs (its "hub"
workspace / VPC), it builds a Databricks ``WorkspaceClient`` for each
configured target workspace — reusing the SAME credentials already resolved
by ``get_target_workspaces`` (no new secrets) — and makes the cheapest
authenticated call there is, ``current_user.me()``.

That single call exercises the whole path: DNS, the cross-VPC / hub-spoke
network route (peering, PrivateLink, security groups), TLS, and auth.

It distinguishes two things the caller usually conflates:

* ``network_reachable`` — did we reach the workspace control plane at all?
  A 401/403 (bad/again-rejected credentials) STILL means the network path
  works; only a connection refusal / timeout / DNS failure means it doesn't.
* ``authenticated`` — did the resolved credentials actually work?

So you can prove "yes, the SDK can reach workspace B from workspace A" even
without valid credentials handy.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.core.workspaces import (
    WorkspaceConfig,
    get_target_workspaces as fetch_workspaces,
    get_workspace_config,
)

logger = logging.getLogger(__name__)

# Substrings that indicate we DID reach the control plane but auth was rejected
# (network path is fine).
_AUTH_MARKERS = (
    "401", "403", "unauthorized", "permission_denied", "permission denied",
    "invalid_token", "invalid access token", "authentication", "could not authenticate",
    "default auth", "forbidden", "expired",
)
# Substrings that indicate we never reached the control plane (network/DNS/TLS).
_NETWORK_MARKERS = (
    "timed out", "timeout", "connection", "name resolution", "getaddrinfo",
    "unreachable", "refused", "max retries", "failed to establish",
    "no route to host", "network is unreachable", "ssl", "handshake",
    "temporarily unavailable", "newconnectionerror", "connecttimeout",
)


def _build_client(ws: WorkspaceConfig, timeout: float):
    """Build a WorkspaceClient for ``ws`` with a bounded retry budget.

    Returns (client, auth_method). Credentials come straight from the
    resolved WorkspaceConfig — service principal preferred, then PAT, then
    the SDK's ambient/default auth chain.
    """
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config

    if ws.client_id and ws.client_secret:
        cfg = Config(
            host=ws.host,
            client_id=ws.client_id,
            client_secret=ws.client_secret,
            retry_timeout_seconds=int(timeout),
        )
        return WorkspaceClient(config=cfg), "service_principal"
    if ws.token:
        cfg = Config(
            host=ws.host,
            token=ws.token,
            auth_type="pat",
            retry_timeout_seconds=int(timeout),
        )
        return WorkspaceClient(config=cfg), "pat"
    cfg = Config(host=ws.host, retry_timeout_seconds=int(timeout))
    return WorkspaceClient(config=cfg), "default"


async def _ping_one(ws: WorkspaceConfig, timeout: float) -> Dict[str, Any]:
    """Ping a single workspace and classify the outcome."""
    result: Dict[str, Any] = {
        "name": ws.name,
        "host": ws.host,
        "environment": ws.environment,
        "credential_source": ws.credential_source,
        "auth_method": None,
        "network_reachable": False,
        "authenticated": False,
        "identity": None,
        "latency_ms": None,
        "status": "error",
        "detail": "",
    }

    if not ws.host:
        result["detail"] = "No host configured for this workspace."
        return result

    try:
        client, auth_method = _build_client(ws, timeout)
        result["auth_method"] = auth_method
    except Exception as e:  # noqa: BLE001
        result["detail"] = f"Failed to build client: {e}"
        return result

    start = time.monotonic()
    try:
        me = await asyncio.wait_for(
            asyncio.to_thread(client.current_user.me),
            timeout=timeout,
        )
        result["latency_ms"] = round((time.monotonic() - start) * 1000)
        result["network_reachable"] = True
        result["authenticated"] = True
        result["status"] = "ok"
        result["identity"] = getattr(me, "user_name", None) or getattr(me, "display_name", None)
        result["detail"] = "Reachable and authenticated."
        return result
    except asyncio.TimeoutError:
        result["latency_ms"] = round((time.monotonic() - start) * 1000)
        result["status"] = "unreachable"
        result["detail"] = (
            f"Timed out after {timeout:.0f}s with no response — the control plane "
            "was not reachable. Likely a network-path issue (VPC peering / "
            "PrivateLink route / security group / NACL) or an incorrect host."
        )
        return result
    except Exception as e:  # noqa: BLE001
        result["latency_ms"] = round((time.monotonic() - start) * 1000)
        msg = str(e)
        low = msg.lower()
        if any(m in low for m in _AUTH_MARKERS):
            # Reached the control plane; it rejected the credentials.
            result["network_reachable"] = True
            result["status"] = "auth_failed"
            result["detail"] = (
                "Network path is OPEN (the workspace responded), but the "
                f"credentials were rejected: {msg}"
            )
        elif any(m in low for m in _NETWORK_MARKERS):
            result["status"] = "unreachable"
            result["detail"] = (
                "Could not reach the workspace control plane — network-path "
                f"issue (VPC peering / PrivateLink / security group): {msg}"
            )
        else:
            result["status"] = "error"
            result["detail"] = f"Unexpected error: {msg}"
        return result


class PingWorkspacesInput(BaseModel):
    target: Optional[str] = Field(
        default=None,
        description=(
            "Optional workspace name or host URL to ping. If omitted, pings ALL "
            "configured target workspaces. A full https:// host not in config is "
            "pinged using the default credentials."
        ),
    )
    timeout_seconds: float = Field(
        default=10,
        ge=2,
        le=60,
        description="Max seconds to wait for each workspace before marking it unreachable.",
    )


@tool(
    name="ping_workspaces",
    description=(
        "Connectivity diagnostic: ping one or all configured target Databricks "
        "workspaces using the Databricks SDK to prove the app can REACH them "
        "(e.g. across VPCs in a hub/spoke topology). Reuses the already-"
        "configured credentials — it does NOT need or change any secrets. For "
        "each workspace it reports network_reachable (was the control plane "
        "reachable at all), authenticated (did the credentials work), the SDK "
        "identity, and latency. NOTE: an auth failure still PROVES the network "
        "path is open. Use this to test cross-workspace/cross-VPC reachability."
    ),
    args_schema=PingWorkspacesInput,
    feature_flag="core",
    friendly_label="Pinging workspace(s)...",
)
async def ping_workspaces(target: Optional[str] = None, timeout_seconds: float = 10) -> Dict[str, Any]:
    # Resolve which workspaces to ping.
    targets: List[WorkspaceConfig]
    if target:
        ws = get_workspace_config(target)
        if not ws:
            return {
                "count": 0,
                "results": [],
                "note": (
                    f"No workspace matched '{target}'. Provide a configured name/host "
                    "(see get_target_workspaces) or a full https:// URL."
                ),
            }
        targets = [ws]
    else:
        targets = fetch_workspaces()

    if not targets:
        return {
            "count": 0,
            "results": [],
            "note": "No target workspaces are configured. Add them under Admin -> Settings -> Target Workspaces.",
        }

    # Ping all targets concurrently — independent network calls.
    results = await asyncio.gather(*[_ping_one(ws, timeout_seconds) for ws in targets])

    reachable = sum(1 for r in results if r["network_reachable"])
    authed = sum(1 for r in results if r["authenticated"])
    note = (
        f"{reachable}/{len(results)} workspace(s) network-reachable; "
        f"{authed} fully authenticated. 'network_reachable' answers the "
        "cross-VPC connectivity question — an auth_failed result still means "
        "the SDK successfully reached that workspace over the network."
    )

    return {
        "count": len(results),
        "results": results,
        "note": note,
    }
