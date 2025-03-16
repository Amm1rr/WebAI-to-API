# src/app/endpoints/init.py
# This file marks the "endpoints" directory as a Python package.
from .gemini import router as gemini_router
from .chat import router as chat_router

__all__ = ["gemini_router", "chat_router"]
