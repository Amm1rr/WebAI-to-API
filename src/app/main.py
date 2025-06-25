# src/app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

from app.services.gemini_client import get_gemini_client
from app.services.session_manager import init_session_managers
from app.logger import logger

# Import endpoint routers
from app.endpoints import gemini, chat

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Initializes services on startup.
    """
    # Initialization logic is handled by the `run.py` script before the app starts.
    # We only initialize session managers here if the client was created successfully.
    if get_gemini_client():
        init_session_managers()
        logger.info("Session managers initialized for WebAI-to-API.")
    
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
