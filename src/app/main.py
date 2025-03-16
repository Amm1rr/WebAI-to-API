# src/app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

from app.services.gemini_client import init_gemini_client, close_gemini_client
from app.services.session_manager import init_session_managers
from app.logger import logger

# Import endpoint routers
from app.endpoints import gemini, chat

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the Gemini client
    await init_gemini_client()
    # Initialize session managers (for translation and chat)
    init_session_managers()
    yield
    # Close the Gemini client during shutdown
    await close_gemini_client()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the endpoint routers
app.include_router(gemini.router)
app.include_router(chat.router)
