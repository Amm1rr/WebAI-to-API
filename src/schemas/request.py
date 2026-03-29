# src/schemas/request.py
from enum import Enum
from typing import Any, List, Optional, Union
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Multimodal content part schemas (OpenAI vision format)
# ---------------------------------------------------------------------------

class ImageUrlDetail(BaseModel):
    """Inner object for image_url content parts."""
    url: str
    detail: Optional[str] = "auto"


class ContentPart(BaseModel):
    """A single part of a multimodal message content array."""
    type: str  # "text" | "image_url"
    text: Optional[str] = None
    image_url: Optional[ImageUrlDetail] = None


# ---------------------------------------------------------------------------
# Gemini model enum
# ---------------------------------------------------------------------------

class GeminiModels(str, Enum):
    """
    Available Gemini models (gemini-webapi >= 1.19.2).
    """

    # Gemini 3.0 Series
    PRO = "gemini-3.0-pro"
    FLASH = "gemini-3.0-flash"
    FLASH_THINKING = "gemini-3.0-flash-thinking"


class GeminiRequest(BaseModel):
    message: str
    model: GeminiModels = Field(default=GeminiModels.FLASH, description="Model to use for Gemini.")
    files: Optional[List[str]] = []

class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    # Accept any string — unknown model names are resolved to the closest
    # GeminiModels value in the endpoint (see _resolve_model in chat.py).
    # This ensures compatibility with Home Assistant and other OpenAI clients
    # that send model names like "gemini-3-pro-image-preview".
    model: Optional[str] = None
    stream: Optional[bool] = False

class Part(BaseModel):
    text: str

class Content(BaseModel):
    parts: List[Part]

class GoogleGenerativeRequest(BaseModel):
    contents: List[Content]
