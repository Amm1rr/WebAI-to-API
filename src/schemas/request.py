# src/schemas/request.py
from enum import Enum
from typing import Any, Dict, List, Optional
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
    model: str = Field(default="gemini-3.0-flash", description="Model to use for Gemini.")
    files: Optional[List[str]] = []

class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[str] = None
    stream: Optional[bool] = False
    tools: Optional[List[dict]] = None
    tool_choice: Optional[Any] = None

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
