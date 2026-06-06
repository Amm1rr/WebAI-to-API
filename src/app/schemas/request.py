# src/app/schemas/request.py
from enum import Enum
from typing import Any, Annotated, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

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

class OpenAIChatFilePayload(BaseModel):
    """File attachment payload for Gemini WebAPI file parts. Supported formats are documented in docs/api.md."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(
        description="Original filename used for validation and attachment handling.",
    )
    file_data: str = Field(
        description=(
            "Base64 data URL in the form data:<mime>;base64,... containing the file bytes. "
            "Remote URLs, filesystem paths, and file_id are not supported."
        ),
    )


class OpenAIChatTextContentPart(BaseModel):
    """OpenAI-style text content part."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = Field(description='Content part discriminator. Must be "text".')
    text: str = Field(description="Plain text for this content part.")


class OpenAIChatFileContentPart(BaseModel):
    """OpenAI-style file attachment content part. File parts are supported only by the Gemini WebAPI backend and are request-scoped."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["file"] = Field(description='Content part discriminator. Must be "file".')
    file: OpenAIChatFilePayload = Field(
        description="File attachment metadata and base64 data URL payload.",
    )


OpenAIChatContentPart = Annotated[
    Union[OpenAIChatTextContentPart, OpenAIChatFileContentPart],
    Field(discriminator="type"),
]


class OpenAIChatMessage(BaseModel):
    """OpenAI-compatible chat message. content may be a plain string or an array of text and file content parts. File parts are supported only by Gemini WebAPI, are request-scoped, and are flattened into prompt text plus attachments. Exact text/file interleaving is not preserved."""

    model_config = ConfigDict(extra="allow")

    role: str = Field(description="Message role such as user, assistant, or system.")
    content: Optional[Union[str, List[OpenAIChatContentPart]]] = Field(
        default=None,
        description=(
            "Either a plain string or an array of content parts. "
            "Text parts are flattened into prompt text. File parts are supported only by Gemini WebAPI, are request-scoped, "
            "and are documented in docs/api.md. Exact text/file interleaving is not preserved."
        ),
    )
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class OpenAIChatRequest(BaseModel):
    """OpenAI-compatible chat request. Gemini WebAPI supports multimodal file content parts; supported formats are documented in docs/api.md."""

    messages: List[OpenAIChatMessage]
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
