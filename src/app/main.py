# src/app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uuid

from app.services.providers.gemini.client import (
    close_gemini_client,
    init_gemini_client,
)
from app.services.providers.gemini.session_manager import (
    init_session_managers,
    shutdown_session_managers,
)
from app.services.browser.auth_manager import get_auth_manager
from app.logger import logger

# Import endpoint routers
from app.endpoints import gemini, chat, google_generative, auth, system, ui

import os
import signal
import threading
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Initializes services on startup.
    """
    # A: FastAPI lifespan startup log
    logger.info("FastAPI application lifespan startup executing.")

    # Initialize Gemini client in server process
    init_result = await init_gemini_client(registry_updater=init_session_managers)
    if init_result:
        logger.info("Gemini client successfully initialized in server process.")
    else:
        logger.error("Failed to initialize Gemini client in server process.")

    # Register Gemini Auth Strategy
    try:
        from app.services.providers.gemini.auth import GeminiAuthStrategy
        gemini_auth_strategy = GeminiAuthStrategy()
        get_auth_manager().register_strategy(gemini_auth_strategy.provider_name, gemini_auth_strategy)
        logger.info("Gemini authentication strategy registered.")
    except Exception as e:
        logger.error(f"Failed to register Gemini auth strategy: {e}")

    # Refresh auth status cache asynchronously on startup
    try:
        get_auth_manager().refresh_status()
        logger.info("Authentication status cache successfully initialized.")
    except Exception as e:
        logger.warning(f"Failed to initialize authentication status cache: {e}")

    yield

    # B: FastAPI lifespan shutdown log
    logger.info("FastAPI application lifespan shutdown executing.")

    # Restore temporary shutdown task-dump diagnostics at DEBUG level for investigation
    try:
        tasks = asyncio.all_tasks()
        curr_task = asyncio.current_task()
        logger.debug(f"[SHUTDOWN-DEBUG] [TASK-DUMP] Total asyncio tasks: {len(tasks)}")

        for idx, task in enumerate(tasks):
            if task is curr_task:
                continue
            cancelling_val = getattr(task, "cancelling", lambda: "N/A")() if hasattr(task, "cancelling") else "N/A"
            logger.debug(
                f"[SHUTDOWN-DEBUG] [TASK-DUMP] Task {idx + 1}:\n"
                f"name={task.get_name()}\n"
                f"done={task.done()}\n"
                f"cancelled={task.cancelled()}\n"
                f"cancelling={cancelling_val}\n"
                f"repr={repr(task)}"
            )
    except Exception as e:
        logger.error(f"[SHUTDOWN-DEBUG] Error during task inspection: {e}", exc_info=True)

    try:
        await shutdown_session_managers()
        logger.info("Gemini session registry closed gracefully.")
    except Exception as e:
        logger.error(f"Error closing Gemini session registry: {e}", exc_info=True)

    try:
        await close_gemini_client()
        logger.info("Gemini WebAPI client closed gracefully.")
    except Exception as e:
        logger.error(f"Error closing Gemini WebAPI client: {e}", exc_info=True)

    # Shutdown logic
    try:
        from app.services.browser.engine import get_existing_browser_engine
        engine = get_existing_browser_engine()
        if engine is None:
            logger.info(
                "BrowserEngine was never initialized; skipping browser close."
            )
        else:
            logger.info("Gracefully closing BrowserEngine during application shutdown...")
            await engine.close(source="application")
            logger.info("BrowserEngine closed gracefully.")
    except Exception as e:
        logger.error(f"Error closing BrowserEngine: {e}", exc_info=True)
    logger.info("Application shutdown complete.")

# Define explicit OpenAPI tag ordering
OPENAPI_TAGS = [
    {"name": "Chat"},
    {"name": "Translation"},
    {"name": "Authentication"},
    {"name": "Compatibility"},
    {"name": "Utilities"},
    {"name": "Legacy"},
    {"name": "System"},
]

app = FastAPI(lifespan=lifespan, openapi_tags=OPENAPI_TAGS)

app.mount(
    "/ui/static",
    StaticFiles(directory=ui.STATIC_DIR),
    name="ui_static",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RequestIDMiddleware:
    """
    Request ID middleware for observability.

    Generates a unique request_id for each HTTP request, stores it in
    request.state for downstream access, and returns it in response header.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4()).replace("-", "_")
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_request_id)


app.add_middleware(RequestIDMiddleware)

# Register the endpoint routers for WebAI-to-API
app.include_router(gemini.router)
app.include_router(chat.router)
app.include_router(google_generative.router)
app.include_router(auth.router)
app.include_router(system.router)
app.include_router(ui.router)
