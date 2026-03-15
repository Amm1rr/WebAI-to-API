# src/schemas/request.py
from typing import List, Optional
from pydantic import BaseModel, Field


class GeminiRequest(BaseModel):
    message: str
    model: str = Field(default="gemini-2.0-flash-exp", description="Model to use for Gemini.")
    files: Optional[List[str]] = []


class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[str] = None
    stream: Optional[bool] = False


class Part(BaseModel):
    text: str


class Content(BaseModel):
    parts: List[Part]


class GoogleGenerativeRequest(BaseModel):
    contents: List[Content]
