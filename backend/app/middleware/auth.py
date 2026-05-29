"""Pure-ASGI authentication / request-context middleware.

Originally implemented as a Starlette ``BaseHTTPMiddleware`` subclass,
but ``BaseHTTPMiddleware`` consumes the downstream response into a queue
and only forwards chunks once the whole response is materialised. That
buffering is invisible for the typical JSON endpoint, but it breaks
``StreamingResponse`` (and especially ``text/event-stream``): every
``yield`` from the generator is held until the generator completes, so
the agent's SSE stream — ``status → tool_call → tool_result →
message`` — arrives as a single batched flush at the end of the turn.
The Self Service chat showed this as "no progress, then 10 seconds
later the running pill flashes green and the answer appears at the
same time."

We rewrote the middleware as a pure-ASGI class. Pure ASGI middleware
just forwards ``send`` calls untouched, so chunks from upstream
streaming responses reach the client immediately. The MCP routes
already had a special early-exit because the team had hit the same
wall there; this implementation makes that workaround unnecessary —
nothing here buffers any path.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from typing import Awaitable, Callable, MutableMapping

from app.core.config import settings
from app.core.logging_formatter import (
    current_client_ip,
    current_correlation_id,
    current_endpoint,
    current_method,
    current_request_id,
    current_user_agent,
    current_user_email,
)

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, object]
Message = MutableMapping[str, object]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


def _parse_headers(scope: Scope) -> dict[str, str]:
    """Lower-case header dict from an ASGI scope.

    ASGI headers are a list of ``(bytes, bytes)`` pairs. We surface them
    as a plain dict for ergonomic ``get(...)`` access; lower-casing
    matches Starlette's ``Headers`` semantics (HTTP header names are
    case-insensitive).
    """
    out: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", []) or []:
        try:
            name = raw_name.decode("latin-1").lower()
            value = raw_value.decode("latin-1")
        except Exception:
            continue
        out[name] = value
    return out


class AuthMiddleware:
    """Extract Databricks Apps auth headers and propagate request
    context for logging.

    Mirrors the surface of the previous ``BaseHTTPMiddleware`` version
    so endpoints that read ``request.state.user`` / ``request.state.token``
    keep working unchanged — Starlette/FastAPI build ``request.state``
    from ``scope["state"]``, and we pre-populate that dict here.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        method = str(scope.get("method", ""))
        headers = _parse_headers(scope)

        req_id = str(uuid.uuid4())
        endpoint_token = current_endpoint.set(path)
        req_id_token = current_request_id.set(req_id)
        method_token = current_method.set(method)

        corr_id = headers.get("x-correlation-id") or headers.get("x-request-id", "N/A")
        corr_id_token = current_correlation_id.set(corr_id)

        client = scope.get("client")
        client_ip = client[0] if isinstance(client, (list, tuple)) and client else None
        x_forwarded_for = headers.get("x-forwarded-for")
        if x_forwarded_for:
            client_ip = x_forwarded_for.split(",")[0].strip()
        ip_token = current_client_ip.set(client_ip)

        agent_token = current_user_agent.set(headers.get("user-agent", "Unknown"))

        user_email_token = None
        try:
            # The MCP routes don't need the auth header parsing (they
            # use their own auth model) and previously had a special
            # bypass to dodge BaseHTTPMiddleware buffering. The bypass
            # is no longer needed since pure-ASGI middleware doesn't
            # buffer, but we still skip the header-extraction work on
            # those routes since it's wasted effort.
            if not path.startswith("/mcp"):
                email = headers.get(
                    "x-forwarded-email", settings.MOCK_USER_EMAIL
                )
                username = headers.get(
                    "x-forwarded-preferred-username", settings.MOCK_USER_NAME
                )
                user_id = headers.get("x-forwarded-user", settings.MOCK_USER_ID)
                user_email_token = current_user_email.set(email)

                obo_token = headers.get("x-forwarded-access-token")
                if obo_token:
                    logger.debug(
                        "AuthMiddleware: OBO Token found in standard header. "
                        f"Length: {len(obo_token)}"
                    )
                else:
                    logger.debug(
                        "AuthMiddleware: OBO Token HEADER MISSING in standard "
                        "location (X-Forwarded-Access-Token). If this is local "
                        "dev, this is expected."
                    )
                if not obo_token and settings.MOCK_USER_TOKEN:
                    obo_token = settings.MOCK_USER_TOKEN
                    logger.debug("AuthMiddleware: Using MOCK_USER_TOKEN")

                # FastAPI / Starlette construct ``request.state`` from
                # ``scope["state"]`` lazily on first access, so writing
                # to it here is equivalent to the old
                # ``request.state.user = ...`` pattern.
                state = scope.setdefault("state", {})  # type: ignore[arg-type]
                state["user"] = {
                    "email": email,
                    "username": username,
                    "id": user_id,
                }
                state["token"] = obo_token

                if obo_token and obo_token.startswith("eyJ"):
                    try:
                        parts = obo_token.split(".")
                        if len(parts) > 1:
                            payload_str = parts[1]
                            payload_str += "=" * (-len(payload_str) % 4)
                            payload_data = json.loads(base64.b64decode(payload_str))
                            scopes = (
                                payload_data.get("scp")
                                or payload_data.get("scope")
                            )
                            logger.info(f"DEBUG AUTH: OBO Token Scopes: {scopes}")
                    except Exception as e:
                        logger.error(
                            f"DEBUG AUTH: Failed to decode token scopes: {e}"
                        )

                if email != settings.MOCK_USER_EMAIL:
                    logger.debug(f"AuthMiddleware: User context set for {email}")

            # Wrap ``send`` so we can see the response status code for
            # logging without having to consume the body — that's the
            # whole point of the rewrite. The body is forwarded
            # untouched, chunk by chunk, in real time.
            start_time = time.time()
            status_code: list[int | None] = [None]
            content_length: list[str] = ["unknown"]

            async def send_wrapper(message: Message) -> None:
                if message["type"] == "http.response.start":
                    status_code[0] = int(message.get("status", 0))  # type: ignore[arg-type]
                    for raw_name, raw_value in message.get("headers", []) or []:  # type: ignore[union-attr]
                        try:
                            if raw_name.decode("latin-1").lower() == "content-length":
                                content_length[0] = raw_value.decode("latin-1")
                        except Exception:
                            pass
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
                process_time = time.time() - start_time
                logger.info(
                    "HTTP_REQUEST: "
                    f"status_code={status_code[0]} "
                    f"duration_ms={round(process_time * 1000, 2)} "
                    f"bytes={content_length[0]}"
                )
            except Exception as e:
                process_time = time.time() - start_time
                logger.error(
                    "HTTP_REQUEST_FAILED: "
                    f"duration_ms={round(process_time * 1000, 2)} "
                    f"error='{e}'",
                    exc_info=True,
                )
                raise
        finally:
            current_endpoint.reset(endpoint_token)
            current_request_id.reset(req_id_token)
            current_method.reset(method_token)
            current_client_ip.reset(ip_token)
            current_user_agent.reset(agent_token)
            current_correlation_id.reset(corr_id_token)
            if user_email_token is not None:
                current_user_email.reset(user_email_token)
