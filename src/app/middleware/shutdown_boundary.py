# src/app/middleware/shutdown_boundary.py
"""
Outermost shutdown-aware ASGI boundary.

Handles Uvicorn graceful-timeout cancellation as expected shutdown
termination rather than application failure.
"""
from __future__ import annotations

import asyncio

from app.logger import logger
from app.shutdown import is_shutdown_requested

# Uvicorn 0.40 cancels with this exact message (server.py:289)
_GRACEFUL_TIMEOUT_MSG = "Task cancelled, timeout graceful shutdown exceeded"


def is_graceful_shutdown_cancel(exc: BaseException) -> bool:
    """
    Narrow classifier for Uvicorn graceful-timeout cancellation.

    Returns True only when:
    - exc is CancelledError
    - generic application shutdown has been requested
    - exc message matches Uvicorn's timeout cancel message

    Isolated for explicit testing; avoids broad string matching elsewhere.
    """
    if not isinstance(exc, asyncio.CancelledError):
        return False
    if not is_shutdown_requested():
        return False
    # Uvicorn 0.40 uses CancelledError(msg) with that exact string
    if not exc.args:
        return False
    return exc.args[0] == _GRACEFUL_TIMEOUT_MSG


class ShutdownBoundaryMiddleware:
    """
    Outermost ASGI boundary that converts graceful-shutdown timeout
    cancellations into proper HTTP protocol completions.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False
        response_complete = False

        async def wrapped_send(message):
            nonlocal response_started, response_complete
            mtype = message.get("type")
            if mtype == "http.response.start":
                response_started = True
            elif mtype == "http.response.body":
                if not message.get("more_body", False):
                    response_complete = True
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        except asyncio.CancelledError as exc:
            # Only handle graceful-timeout cancellation during shutdown
            if not is_graceful_shutdown_cancel(exc):
                raise

            # Shutdown timeout cancellation is expected, not an error
            if not response_started:
                logger.info("Graceful shutdown: completing cancelled pre-header request with 503")
                body = b"Server shutting down"
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [
                            (b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close"),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": body,
                        "more_body": False,
                    }
                )
                return
            else:
                if response_complete:
                    return
                logger.info("Graceful shutdown: truncating streaming response without [DONE]")
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    }
                )
                return
