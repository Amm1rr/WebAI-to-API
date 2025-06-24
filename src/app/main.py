# src/app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

from app.services.gemini_client import get_gemini_client, close_gemini_client
from app.services.session_manager import init_session_managers
from app.logger import logger

# Import endpoint routers
from app.endpoints import gemini, chat

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialization logic is now in run.py
    # We only initialize session managers if the client was created successfully.
    if get_gemini_client():
        init_session_managers()
        logger.info("Session managers initialized for WebAI-to-API.")
    yield
    # Close the Gemini client during shutdown (if it was created)
    await close_gemini_client()

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