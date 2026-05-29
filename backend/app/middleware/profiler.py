"""Pure-ASGI Pyinstrument profiling middleware.

The previous ``BaseHTTPMiddleware`` implementation buffered every
response — even non-profiling ones — because that's a property of how
``BaseHTTPMiddleware`` works in Starlette. That buffering broke our
agent's SSE stream (events arrived in a single batch instead of
streaming). See ``app/middleware/auth.py`` for the longer write-up.

This rewrite is pure ASGI: when ``?profile=true`` isn't set we just
forward the call untouched (no buffering). When profiling *is*
requested we collect the response body — profiling intentionally
captures the full response anyway — and replace it with the
Pyinstrument HTML report.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, MutableMapping
from urllib.parse import parse_qs

from fastapi.responses import HTMLResponse
from pyinstrument import Profiler

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, object]
Message = MutableMapping[str, object]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class PyinstrumentMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        if path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        # Cheap query-string sniff — only spin up Pyinstrument when the
        # caller actually asked for it. Most requests fall through here.
        query_string = scope.get("query_string", b"")
        if isinstance(query_string, bytes):
            try:
                qs = query_string.decode("latin-1")
            except Exception:
                qs = ""
        else:
            qs = str(query_string)
        params = parse_qs(qs)
        if params.get("profile", [""])[0] != "true":
            await self.app(scope, receive, send)
            return

        # Profiling path: capture the full response so we can replace
        # it with the HTML report. This intentionally buffers (we're
        # throwing the body away anyway), so the SSE-streaming concern
        # doesn't apply on this branch.
        profiler = Profiler(interval=0.001)
        profiler.start()
        try:
            async def discard_send(_message: Message) -> None:
                # Drop the original response — we'll replace it with
                # the profiler's HTML report.
                return

            try:
                await self.app(scope, receive, discard_send)
            finally:
                profiler.stop()
        except Exception:
            profiler.stop()
            raise

        response = HTMLResponse(profiler.output_html())
        await response(scope, receive, send)  # type: ignore[arg-type]
