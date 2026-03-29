# src/app/main.py
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.services.gemini_client import get_gemini_client, init_gemini_client, GeminiClientNotInitializedError, start_cookie_persister, stop_cookie_persister
from app.services.session_manager import init_session_managers
from app.services.log_broadcaster import SSELogBroadcaster, BroadcastLogHandler
from app.services.stats_collector import StatsCollector
from app.logger import logger

# Import endpoint routers
from app.endpoints import gemini, chat, google_generative, files, responses
from app.endpoints import admin, admin_api

_SRC_DIR = Path(__file__).resolve().parent.parent  # points to src/


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Initializes services on startup.
    """
    # Initialize log broadcaster and attach handler to root logger only.
    # Child loggers (app, uvicorn, etc.) propagate to root by default,
    # so attaching to root is sufficient and avoids duplicate entries.
    broadcaster = SSELogBroadcaster.get_instance()
    handler = BroadcastLogHandler(broadcaster)
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)

    # Try to get the existing client first
    client_initialized = False
    try:
        get_gemini_client()
        client_initialized = True
        logger.info("Gemini client found (initialized in main process).")
    except GeminiClientNotInitializedError:
        logger.info("Gemini client not initialized in worker process, attempting reinitialization...")

    # If client is not available, try to initialize it (for multiprocessing support)
    if not client_initialized:
        try:
            init_result = await init_gemini_client()
            if init_result:
                logger.info("Gemini client successfully initialized in worker process.")
            else:
                logger.error("Failed to initialize Gemini client in worker process.")
        except Exception as e:
            logger.error(f"Error initializing Gemini client in worker process: {e}")

    # Initialize session managers only if the client is available
    try:
        get_gemini_client()
        init_session_managers()
        start_cookie_persister()
        logger.info("Session managers initialized for WebAI-to-API.")
    except GeminiClientNotInitializedError as e:
        logger.warning(f"Session managers not initialized: {e}")

    yield

    # Cleanup on shutdown
    stop_cookie_persister()
    logging.getLogger().removeHandler(handler)
    logger.info("Application shutdown complete.")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the endpoint routers for WebAI-to-API
app.include_router(gemini.router)
app.include_router(chat.router)
app.include_router(google_generative.router)
app.include_router(files.router)
app.include_router(responses.router)

# Register admin routers
app.include_router(admin.router)
app.include_router(admin_api.router)

# Mount static files for admin UI
app.mount("/static", StaticFiles(directory=str(_SRC_DIR / "static")), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/admin")


# Stats middleware - track API requests (skip static/admin)
@app.middleware("http")
async def stats_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/static") and not path.startswith("/admin") and not path.startswith("/api/admin"):
        StatsCollector.get_instance().record_request(path, response.status_code)
    return response
