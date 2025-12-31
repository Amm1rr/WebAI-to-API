# src/schemas/request.py
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

class GeminiModels(str, Enum):
    """
    An enumeration of the available Gemini models.
    """

    # Gemini 3.0 Series
    PRO_3_0 = "gemini-3.0-pro"

    # Gemini 2.5 Series
    PRO_2_5 = "gemini-2.5-pro"
    FLASH_2_5 = "gemini-2.5-flash"


class GeminiRequest(BaseModel):
    message: str
    model: GeminiModels = Field(default=GeminiModels.FLASH_2_5, description="Model to use for Gemini.")
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
