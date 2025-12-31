# src/app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

from app.services.gemini_client import get_gemini_client, init_gemini_client, GeminiClientNotInitializedError
from app.services.session_manager import init_session_managers
from app.logger import logger

# Import endpoint routers
from app.endpoints import gemini, chat, google_generative

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Initializes services on startup.
    """
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
        logger.info("Session managers initialized for WebAI-to-API.")
    except GeminiClientNotInitializedError as e:
        logger.warning(f"Session managers not initialized: {e}")

    yield

    # Shutdown logic: No explicit client closing is needed anymore.
    # The underlying HTTPX client manages its connection pool automatically.
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
