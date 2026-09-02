import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.shutdown_boundary import ShutdownBoundaryMiddleware, is_graceful_shutdown_cancel
from app import shutdown as shutdown_module


@pytest.fixture(autouse=True)
def reset_shutdown():
    shutdown_module._reset_for_tests()
    yield
    shutdown_module._reset_for_tests()


# --- classifier -------------------------------------------------------------

def test_classifier_exact_graceful_timeout_true_when_shutdown():
    shutdown_module.request_shutdown("application")
    exc = asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")
    assert is_graceful_shutdown_cancel(exc) is True


def test_classifier_same_cancel_without_shutdown_false():
    exc = asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")
    assert is_graceful_shutdown_cancel(exc) is False


def test_classifier_unrelated_cancel_during_shutdown_false():
    shutdown_module.request_shutdown("application")
    exc = asyncio.CancelledError("unrelated cancel")
    assert is_graceful_shutdown_cancel(exc) is False
    exc2 = asyncio.CancelledError()
    assert is_graceful_shutdown_cancel(exc2) is False


def test_classifier_normal_cancel_outside_shutdown_propagates():
    exc = asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")
    assert is_graceful_shutdown_cancel(exc) is False


# --- pre-header -------------------------------------------------------------

def test_pre_header_timeout_cancellation_sends_503():
    app = FastAPI()

    @app.get("/probe")
    async def probe():
        raise asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")

    wrapped = ShutdownBoundaryMiddleware(app)
    shutdown_module.request_shutdown("application")

    client = TestClient(wrapped, raise_server_exceptions=False)
    resp = client.get("/probe")
    assert resp.status_code == 503
    assert resp.headers.get("connection") == "close"
    assert resp.text == "Server shutting down"
    body = b"Server shutting down"
    assert int(resp.headers["content-length"]) == len(body)
    assert resp.content == body


@pytest.mark.asyncio
async def test_pre_header_unrelated_cancel_during_shutdown_propagates():
    async def downstream(scope, receive, send):
        raise asyncio.CancelledError("unrelated cancel")

    wrapped = ShutdownBoundaryMiddleware(downstream)
    shutdown_module.request_shutdown("application")
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}

    async def receive():
        return {"type": "http.disconnect"}

    async def send(msg):
        pass

    with pytest.raises(asyncio.CancelledError, match="unrelated cancel"):
        await wrapped(scope, receive, send)


@pytest.mark.asyncio
async def test_pre_header_same_message_without_shutdown_propagates():
    async def downstream(scope, receive, send):
        raise asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")

    wrapped = ShutdownBoundaryMiddleware(downstream)
    # no shutdown
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}

    async def receive():
        return {"type": "http.disconnect"}

    async def send(msg):
        pass

    with pytest.raises(asyncio.CancelledError):
        await wrapped(scope, receive, send)


@pytest.mark.asyncio
async def test_pre_header_normal_cancellation_propagates_via_middleware():
    async def downstream(scope, receive, send):
        raise asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")

    wrapped = ShutdownBoundaryMiddleware(downstream)
    # no shutdown → must re-raise
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}

    async def receive():
        return {"type": "http.disconnect"}

    async def send(msg):
        pass

    with pytest.raises(asyncio.CancelledError):
        await wrapped(scope, receive, send)


# --- streaming / post-header ------------------------------------------------

@pytest.mark.asyncio
async def test_post_header_sends_terminating_frame():
    messages = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [[b"content-type", b"text/event-stream"]]})
        await send({"type": "http.response.body", "body": b"data: first chunk\n\n", "more_body": True})
        raise asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")

    wrapped = ShutdownBoundaryMiddleware(downstream)
    shutdown_module.request_shutdown("application")

    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}

    async def receive():
        return {"type": "http.disconnect"}

    async def send(msg):
        messages.append(msg)

    await wrapped(scope, receive, send)

    assert messages[0]["type"] == "http.response.start"
    assert messages[1]["body"] == b"data: first chunk\n\n"
    assert messages[1]["more_body"] is True
    assert messages[2]["type"] == "http.response.body"
    assert messages[2]["body"] == b""
    assert messages[2]["more_body"] is False
    all_bodies = b"".join(m.get("body", b"") for m in messages)
    assert b"[DONE]" not in all_bodies


@pytest.mark.asyncio
async def test_already_complete_no_duplicate_frame():
    messages = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done", "more_body": False})
        raise asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")

    wrapped = ShutdownBoundaryMiddleware(downstream)
    shutdown_module.request_shutdown("application")
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}

    async def receive():
        return {"type": "http.disconnect"}

    async def send(msg):
        messages.append(msg)

    await wrapped(scope, receive, send)
    assert len(messages) == 2
    assert messages[1]["more_body"] is False


@pytest.mark.asyncio
async def test_cleanup_ordering_finally_before_response():
    order = []

    async def downstream(scope, receive, send):
        try:
            raise asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")
        finally:
            order.append("downstream_finally")

    wrapped = ShutdownBoundaryMiddleware(downstream)
    shutdown_module.request_shutdown("application")
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}

    async def receive():
        return {"type": "http.disconnect"}

    messages = []

    async def send(msg):
        messages.append(msg)
        if msg["type"] == "http.response.start":
            order.append("response_sent")

    await wrapped(scope, receive, send)
    assert order == ["downstream_finally", "response_sent"]


def test_non_http_passthrough():
    called = {}

    async def downstream(scope, receive, send):
        called["scope"] = scope["type"]
        raise asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded")

    wrapped = ShutdownBoundaryMiddleware(downstream)
    shutdown_module.request_shutdown("application")

    scope = {"type": "lifespan"}

    async def receive():
        return {}

    async def send(msg):
        pass

    async def run():
        await wrapped(scope, receive, send)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert called["scope"] == "lifespan"


def test_middleware_ordering_outermost_user():
    from app.main import app

    # user_middleware is list of Starlette Middleware objects, outermost user is index 0
    # ServerErrorMiddleware is framework-owned outermost, not in user_middleware
    user_classes = [m.cls.__name__ for m in app.user_middleware]
    # ShutdownBoundaryMiddleware must be outermost user (first in list due to insert(0, ...))
    assert user_classes[0] == "ShutdownBoundaryMiddleware", f"expected Shutdown outermost, got {user_classes}"
    # It should wrap CORS and RequestID
    assert "CORSMiddleware" in user_classes
    assert "RequestIDMiddleware" in user_classes
    # Verify Shutdown wraps them: index 0 is Shutdown, RequestID/CORS after
    shutdown_idx = user_classes.index("ShutdownBoundaryMiddleware")
    cors_idx = user_classes.index("CORSMiddleware")
    req_idx = user_classes.index("RequestIDMiddleware")
    assert shutdown_idx < cors_idx
    assert shutdown_idx < req_idx
