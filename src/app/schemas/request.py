# src/app/schemas/request.py
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class GeminiModels(str, Enum):
    """
    An enumeration of the available Gemini models.
    """

    # Gemini 3 Series
    PRO = "gemini-3-pro"
    FLASH = "gemini-3-flash"
    FLASH_THINKING = "gemini-3-flash-thinking"

    # Default model
    DEFAULT = "unspecified"
    


class GeminiRequest(BaseModel):
    message: str
    model: str = Field(default="gemini-3-flash", description="Model to use for Gemini.")
    files: Optional[List[str]] = []
    gem: Optional[str] = Field(default=None, examples=[None], description="Gem ID or name to use as system prompt.")
    stream: Optional[bool] = False
    conversation_id: Optional[str] = Field(default=None, description="Cryptographically secure token to maintain chat state.")

class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[str] = None
    provider: Optional[str] = None
    stream: Optional[bool] = False
    tools: Optional[List[dict]] = None
    tool_choice: Optional[Any] = None
    gem: Optional[str] = Field(default=None, description="Gem ID or name to use as system prompt.")
    conversation_id: Optional[str] = Field(default=None, description="ID to continue an existing browser conversation.")

class Part(BaseModel):
    text: Optional[str] = None
    functionCall: Optional[Dict[str, Any]] = None
    functionResponse: Optional[Dict[str, Any]] = None

class Content(BaseModel):
    parts: List[Part]
    role: Optional[str] = None

class FunctionDeclaration(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None

class Tool(BaseModel):
    functionDeclarations: Optional[List[FunctionDeclaration]] = None

class GoogleGenerativeRequest(BaseModel):
    contents: List[Content]
    tools: Optional[List[Tool]] = None
    systemInstruction: Optional[Any] = None
    generationConfig: Optional[Dict[str, Any]] = None
