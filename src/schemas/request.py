# src/schemas/request.py
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

class GeminiModels(str, Enum):
    """
    An enumeration of the available Gemini models.
    """

    # Gemini 3.1 Series
    PRO_3_1 = "gemini-3.1-pro"

    # Gemini 3.0 Series
    FLASH_3_0 = "gemini-3.0-flash"
    FLASH_3_0_THINKING = "gemini-3.0-flash-thinking"


class GeminiRequest(BaseModel):
    message: str
    model: GeminiModels = Field(default=GeminiModels.FLASH_3_0, description="Model to use for Gemini.")
    files: Optional[List[str]] = []

class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[GeminiModels] = None
    stream: Optional[bool] = False

class Part(BaseModel):
    text: str

class Content(BaseModel):
    parts: List[Part]

class GoogleGenerativeRequest(BaseModel):
    contents: List[Content]
