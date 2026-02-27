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
    FLASH_3_0 = "gemini-3.0-flash"
    FLASH_3_0_THINKING = "gemini-3.0-flash-thinking"
    
    # Default model
    DEFAULT = "unspecified"
    


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
