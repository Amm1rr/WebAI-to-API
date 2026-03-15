# src/models/gemini.py
from typing import Optional, List, Union
from pathlib import Path
from gemini_webapi import GeminiClient as WebGeminiClient
from app.config import CONFIG


MODEL_ALIASES = {
    # short names for CCR model selection
    "flash": "gemini-2.0-flash-exp",
    "fast": "gemini-2.0-flash-exp",
    "thinking": "gemini-2.0-exp-advanced",
    "pro": "gemini-1.5-pro",
    # compatibility aliases
    "gemini-2.5-flash": "gemini-2.0-flash-exp",
    "gemini-2.5-pro": "gemini-1.5-pro",
    "gemini-3.0-pro": "gemini-1.5-pro",
    "gemini-2.0-flash-thinking": "gemini-2.0-exp-advanced",
    "gemini-2.0-flash-thinking-with-apps": "gemini-2.0-exp-advanced",
}


def resolve_model_name(model: str) -> str:
    return MODEL_ALIASES.get(model, model)


class MyGeminiClient:
    """
    Wrapper for the Gemini Web API client.
    """
    def __init__(self, secure_1psid: str, secure_1psidts: str, proxy: str | None = None) -> None:
        self.client = WebGeminiClient(secure_1psid, secure_1psidts, proxy)

    async def init(self) -> None:
        """Initialize the Gemini client."""
        await self.client.init()

    async def generate_content(self, message: str, model: str, files: Optional[List[Union[str, Path]]] = None):
        """
        Generate content using the Gemini client.
        """
        resolved_model = resolve_model_name(model)
        return await self.client.generate_content(message, model=resolved_model, files=files)

    async def close(self) -> None:
        """Close the Gemini client."""
        await self.client.close()

    def start_chat(self, model: str):
        """
        Start a chat session with the given model.
        """
        resolved_model = resolve_model_name(model)
        return self.client.start_chat(model=resolved_model)
