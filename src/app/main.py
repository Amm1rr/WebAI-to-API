# src/app/main.py
from fastapi import FastAPI, Request, Response
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uuid

from app.services.providers.gemini.client import init_gemini_client, GeminiClientNotInitializedError
from app.services.providers.gemini.session_manager import init_session_managers
from app.services.browser.auth_manager import get_auth_manager
from app.logger import logger

# Import endpoint routers
from app.endpoints import gemini, chat, google_generative, auth, system

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
    init_result = await init_gemini_client()
    if init_result:
        logger.info("Gemini client successfully initialized in server process.")
    else:
        logger.error("Failed to initialize Gemini client in server process.")

    # Register Gemini Auth Strategy
    try:
        from app.services.providers.gemini.auth import GeminiAuthStrategy
        get_auth_manager().set_strategy(GeminiAuthStrategy())
        logger.info("Gemini authentication strategy registered.")
    except Exception as e:
        logger.error(f"Failed to register Gemini auth strategy: {e}")

    # Initialize session managers
    try:
        await init_session_managers()
        logger.info("Session managers initialized for WebAI-to-API.")
    except GeminiClientNotInitializedError as e:
        logger.warning(f"Session managers not initialized: {e}")

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

    # Shutdown logic
    logger.info("Gracefully closing BrowserEngine during application shutdown...")
    try:
        from app.services.browser.engine import get_browser_engine
        engine = await get_browser_engine()
        await engine.close()
        logger.info("BrowserEngine closed gracefully.")
    except Exception as e:
        logger.error(f"Error closing BrowserEngine: {e}", exc_info=True)
    logger.info("Application shutdown complete.")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """
    Request ID middleware for observability.

    Generates a unique request_id for each HTTP request, stores it in
    request.state for downstream access, and returns it in response header.
    """
    request_id = str(uuid.uuid4()).replace("-", "_")
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

# Register the endpoint routers for WebAI-to-API
app.include_router(gemini.router)
app.include_router(chat.router)
app.include_router(google_generative.router)
app.include_router(auth.router)
app.include_router(system.router)
