# src/schemas/request.py
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

class GeminiModels(str, Enum):
    """
    An enumeration of the available Gemini models.
    Matches model names from gemini-webapi >= 2.0.0.
    """

    # Gemini 3 Series
    PRO_3 = "gemini-3-pro"
    FLASH_3 = "gemini-3-flash"
    FLASH_3_THINKING = "gemini-3-flash-thinking"

    # Gemini 3 Plus Series
    PRO_3_PLUS = "gemini-3-pro-plus"
    FLASH_3_PLUS = "gemini-3-flash-plus"
    FLASH_3_THINKING_PLUS = "gemini-3-flash-thinking-plus"

    # Gemini 3 Advanced Series
    PRO_3_ADVANCED = "gemini-3-pro-advanced"
    FLASH_3_ADVANCED = "gemini-3-flash-advanced"
    FLASH_3_THINKING_ADVANCED = "gemini-3-flash-thinking-advanced"

    # Unspecified (use server default)
    UNSPECIFIED = "unspecified"


class GeminiRequest(BaseModel):
    message: str
    model: GeminiModels = Field(default=GeminiModels.FLASH_3, description="Model to use for Gemini.")
    files: Optional[List[str]] = []
    gem: Optional[str] = Field(default=None, description="Gem ID or name to use as system prompt.")

class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[GeminiModels] = None
    stream: Optional[bool] = False
    gem: Optional[str] = Field(default=None, description="Gem ID or name to use as system prompt.")

class Part(BaseModel):
    text: str

class Content(BaseModel):
    parts: List[Part]

class GoogleGenerativeRequest(BaseModel):
    contents: List[Content]
