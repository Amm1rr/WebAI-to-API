# src/app/services/session_manager.py
import asyncio
from app.logger import logger
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError

class SessionManager:
    def __init__(self, client):
        self.client = client
        self.session = None
        self.model = None
        self.lock = asyncio.Lock()

    async def get_response(self, model, message, images):
        async with self.lock:
            # Start a new session if none exists or the model has changed
            if self.session is None or self.model != model:
                if self.session is not None:
                    # Closing the session is handled by the library's internal logic
                    pass
                # If model is an Enum, use its value
                model_value = model.value if hasattr(model, "value") else model
                self.session = self.client.start_chat(model=model_value)
                self.model = model

            try:
                # FIX: The underlying library `gemini-webapi` has changed its keyword arguments
                # in a recent update. `message` is now `prompt` and `images` is now `files`.
                return await self.session.send_message(prompt=message, files=images)
            except Exception as e:
                logger.error(f"Error in session get_response: {e}", exc_info=True)
                raise

_translate_session_manager = None
_gemini_chat_manager = None

def init_session_managers():
    """
    Initialize session managers for translation and chat
    """
    global _translate_session_manager, _gemini_chat_manager
    try:
        client = get_gemini_client()
        _translate_session_manager = SessionManager(client)
        _gemini_chat_manager = SessionManager(client)
    except GeminiClientNotInitializedError:
        logger.warning("Session managers not initialized: Gemini client not available.")

def get_translate_session_manager():
    return _translate_session_manager

def get_gemini_chat_manager():
    return _gemini_chat_manager
