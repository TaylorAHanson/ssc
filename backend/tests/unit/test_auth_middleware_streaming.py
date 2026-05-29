"""Streaming-behavior regression tests for the auth middleware.

The original ``BaseHTTPMiddleware`` implementation silently broke
``StreamingResponse`` — every chunk a route ``yield``ed got captured
into an internal queue and forwarded only after the generator
completed. That produced the user-visible bug "the agent's running
pill, success pill, and final answer all pop in at once instead of
streaming." This module asserts the property we actually care about
at the middleware layer: when the downstream app calls ``send`` with
real-time gaps between chunks, the middleware forwards each call
immediately rather than collecting them.

We can't use ``httpx.ASGITransport`` for this — it buffers chunks
itself for testing convenience — so we drive the middleware directly
with hand-rolled ASGI primitives and time the ``send`` calls our
wrapper makes downstream.
"""
from __future__ import annotations

import asyncio
import time

from app.middleware.auth import AuthMiddleware


CHUNK_GAP_MS = 80


async def _streaming_app(scope, receive, send):
    """Tiny ASGI app that emits three body chunks with sleeps between
    them, mirroring how a ``StreamingResponse`` driving an SSE event
    generator would call ``send``.
    """
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
    })
    await send({"type": "http.response.body", "body": b"chunk-0\n", "more_body": True})
    await asyncio.sleep(CHUNK_GAP_MS / 1000)
    await send({"type": "http.response.body", "body": b"chunk-1\n", "more_body": True})
    await asyncio.sleep(CHUNK_GAP_MS / 1000)
    await send({"type": "http.response.body", "body": b"chunk-2\n", "more_body": False})


def _make_scope() -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/sse",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
    }


def test_auth_middleware_does_not_buffer_streaming_response():
    """Each downstream ``send`` must reach the outer ``send`` with the
    inter-chunk gaps preserved. ``BaseHTTPMiddleware`` would collapse
    them to ~0ms by buffering body messages until the response
    generator completes.
    """
    middleware = AuthMiddleware(_streaming_app)

    body_times: list[float] = []

    async def receive():
        # The downstream app never calls receive in this test, but
        # ASGI requires it to be callable.
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message):
        if message.get("type") == "http.response.body":
            body_times.append(time.monotonic())

    asyncio.run(middleware(_make_scope(), receive, send))

    assert len(body_times) == 3, (
        f"Expected 3 body chunks, got {len(body_times)}"
    )
    spread_ms = (body_times[-1] - body_times[0]) * 1000
    # Chunks are emitted ``CHUNK_GAP_MS`` apart twice → expected
    # spread ≈ 2 × CHUNK_GAP_MS. The lower bound of 1× leaves room
    # for scheduling jitter on slow CI hosts; with
    # ``BaseHTTPMiddleware`` this number was ~0.
    assert spread_ms > CHUNK_GAP_MS, (
        f"Streaming response was buffered: chunks arrived in "
        f"{spread_ms:.0f}ms (expected >{CHUNK_GAP_MS}ms). "
        "Did AuthMiddleware regress to BaseHTTPMiddleware?"
    )


def test_auth_middleware_propagates_user_state():
    """``request.state.user`` and ``request.state.token`` must be
    populated by the middleware so downstream endpoints can read them.
    Pure-ASGI middleware writes to ``scope['state']`` which Starlette
    surfaces as ``request.state``.
    """
    captured: dict = {}

    async def app(scope, receive, send):
        captured["state"] = dict(scope.get("state", {}))
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [],
        })
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = AuthMiddleware(app)
    scope = _make_scope()
    scope["headers"] = [
        (b"x-forwarded-email", b"taylor@example.com"),
        (b"x-forwarded-preferred-username", b"taylor"),
        (b"x-forwarded-user", b"u-123"),
    ]

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        pass

    asyncio.run(middleware(scope, receive, send))

    assert captured["state"].get("user") == {
        "email": "taylor@example.com",
        "username": "taylor",
        "id": "u-123",
    }
