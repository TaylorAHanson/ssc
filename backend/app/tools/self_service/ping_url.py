"""
Tool: ping an arbitrary URL to prove the APP can reach it over the network.

A general-purpose connectivity diagnostic (the sibling of ``ping_workspaces``,
which is Databricks-only). From wherever the app runs, it makes a single HTTP
request to the given URL and classifies the outcome, separating two things
callers usually conflate:

* ``network_reachable`` — did we reach the host at all? A 401/403/404/405 (or
  any HTTP response) STILL proves the network path works; only a DNS failure /
  connection refusal / connect-timeout means it doesn't.
* the HTTP ``status`` — what the server said once reached.

It sends NO credentials, so it can't prove auth — but for "can this app reach
host X" (e.g. an internal API gateway across the corporate egress) that's
exactly the question it answers. Use ``verify_tls=false`` for hosts behind an
internal CA (a TLS cert error still means the host was reached).
"""
import logging
import time
from typing import Any, Dict
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from app.tools.mcp import tool

logger = logging.getLogger(__name__)


class PingUrlInput(BaseModel):
    url: str = Field(..., description="Full URL to ping, e.g. https://apigw-op.example.com/iam/v1/health.")
    method: str = Field(
        default="GET",
        description="HTTP method to use: GET or HEAD (read-only probes only).",
    )
    timeout_seconds: float = Field(
        default=10, ge=2, le=60,
        description="Max seconds to wait before marking the host unreachable.",
    )
    verify_tls: bool = Field(
        default=True,
        description=("Verify the TLS certificate. Set false for hosts behind an internal CA — "
                     "a cert error still proves the host was reached over the network."),
    )


@tool(
    name="ping_url",
    description=(
        "Connectivity diagnostic: make a single HTTP request from the app to any "
        "URL to prove the app can REACH that host over the network (e.g. an "
        "internal API gateway across corporate egress). Reports network_reachable "
        "(did we reach the host at all — any HTTP response, even 401/403/404, "
        "counts as reachable), the HTTP status code, and latency. Sends NO "
        "credentials. Use verify_tls=false for internal-CA hosts (a TLS error "
        "still proves reachability)."
    ),
    args_schema=PingUrlInput,
    feature_flag="core",
    friendly_label="Pinging URL...",
)
async def ping_url(url: str, method: str = "GET", timeout_seconds: float = 10,
                   verify_tls: bool = True) -> Dict[str, Any]:
    method = (method or "GET").upper()
    if method not in ("GET", "HEAD"):
        return {"status": "error", "detail": f"Unsupported method '{method}'. Use GET or HEAD."}

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"status": "error", "detail": f"Invalid URL '{url}'. Must be an http(s):// URL."}

    result: Dict[str, Any] = {
        "url": url,
        "method": method,
        "verify_tls": verify_tls,
        "network_reachable": False,
        "status_code": None,
        "latency_ms": None,
        "status": "error",
        "detail": "",
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout_seconds,
                                     follow_redirects=True) as client:
            resp = await client.request(method, url)
        result["latency_ms"] = round((time.monotonic() - start) * 1000)
        result["network_reachable"] = True
        result["status_code"] = resp.status_code
        code = resp.status_code
        if code in (401, 403):
            result["status"] = "auth_required"
            result["detail"] = (
                f"Reached the host (HTTP {code}) — the network path is OPEN. The "
                "endpoint requires credentials (none were sent)."
            )
        elif 200 <= code < 400:
            result["status"] = "ok"
            result["detail"] = f"Reachable — HTTP {code}."
        else:
            result["status"] = "http_error"
            result["detail"] = (
                f"Reached the host (HTTP {code}) — the network path is OPEN; the "
                "server returned a non-success status."
            )
        return result
    except httpx.ConnectTimeout:
        result["latency_ms"] = round((time.monotonic() - start) * 1000)
        result["status"] = "unreachable"
        result["detail"] = (
            f"Connect timed out after {timeout_seconds:.0f}s — could not establish a "
            "connection. Likely a network-path/egress issue or wrong host."
        )
        return result
    except (httpx.ReadTimeout, httpx.PoolTimeout):
        result["latency_ms"] = round((time.monotonic() - start) * 1000)
        result["network_reachable"] = True
        result["status"] = "timeout"
        result["detail"] = (
            "Connected to the host but it did not respond in time — the network "
            "path is OPEN; the server was slow or the path stalled after connect."
        )
        return result
    except httpx.ConnectError as e:
        result["latency_ms"] = round((time.monotonic() - start) * 1000)
        low = str(e).lower()
        if any(m in low for m in ("ssl", "certificate", "cert_", "tls", "handshake")):
            # A TLS/cert failure means we DID reach the host (TCP + TLS started).
            result["network_reachable"] = True
            result["status"] = "tls_error"
            result["detail"] = (
                f"Reached the host but TLS failed: {e}. The network path is OPEN — "
                "retry with verify_tls=false if this host uses an internal CA."
            )
        else:
            result["status"] = "unreachable"
            result["detail"] = (
                f"Could not reach the host (DNS/refused/route): {e}. Likely a "
                "network-path/egress issue or wrong host."
            )
        return result
    except Exception as e:  # noqa: BLE001
        result["latency_ms"] = round((time.monotonic() - start) * 1000)
        result["status"] = "error"
        result["detail"] = f"Unexpected error: {e}"
        return result
