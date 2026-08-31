# src/app/schemas/request.py
from typing import Any, Annotated, Dict, List, Literal, Optional, Self, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

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


class OpenAIStreamOptions(BaseModel):
    """OpenAI streaming options currently accepted for compatibility."""

    model_config = ConfigDict(extra="forbid")

    include_usage: Optional[StrictBool] = Field(
        default=None,
        description=(
            "Request usage in the final streaming chunk. Current backends may "
            "accept this option without returning usage."
        ),
    )


class OpenAIResponseFormatJSONSchema(BaseModel):
    """JSON Schema payload used by OpenAI structured-output requests."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    description: Optional[str] = None
    schema_: Dict[str, Any] = Field(alias="schema")
    strict: Optional[StrictBool] = None


class OpenAIResponseFormat(BaseModel):
    """OpenAI response format request shape, without backend semantics."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "json_object", "json_schema"]
    json_schema: Optional[OpenAIResponseFormatJSONSchema] = None

    @model_validator(mode="after")
    def validate_json_schema_payload(self) -> Self:
        if self.type == "json_schema" and self.json_schema is None:
            raise ValueError("response_format.json_schema is required for type 'json_schema'.")
        if self.type != "json_schema" and self.json_schema is not None:
            raise ValueError("response_format.json_schema is only valid for type 'json_schema'.")
        return self


def validate_openai_tool_declarations(tools: Any) -> Any:
    """Validate the structural shape consumed by OpenAI-compatible tool paths."""
    if tools is None:
        return tools
    if not isinstance(tools, list):
        raise ValueError("Invalid tool declaration: tools must be a list.")

    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError("Invalid tool declaration: each tool must be an object.")
        if tool.get("type") != "function":
            raise ValueError("Invalid tool declaration: type must be 'function'.")

        function = tool.get("function")
        if not isinstance(function, dict):
            raise ValueError("Invalid tool declaration: function must be an object.")

        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid tool declaration: function.name must be a non-empty string.")

    return tools


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


class GeminiProviderOptions(BaseModel):
    """Gemini-specific request options."""

    model_config = ConfigDict(extra="forbid")

    extended_thinking: Optional[StrictBool] = None


class ProviderOptions(BaseModel):
    """Provider-scoped options for OpenAI-compatible requests."""

    model_config = ConfigDict(extra="forbid")

    gemini: Optional[GeminiProviderOptions] = None


class OpenAIToolChoiceFunction(BaseModel):
    """Function selector inside an OpenAI tool-choice request."""

    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1)


class OpenAIToolChoiceFunctionSelection(BaseModel):
    """Explicit function selector accepted by Chat Completions."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["function"]
    function: OpenAIToolChoiceFunction


OpenAIToolChoice = Union[
    Literal["none", "auto", "required"],
    OpenAIToolChoiceFunctionSelection,
]


class OpenAIChatRequest(BaseModel):
    """OpenAI-compatible chat request. Gemini WebAPI supports multimodal file content parts; supported formats are documented in docs/api.md."""

    messages: List[OpenAIChatMessage]
    model: Optional[str] = None
    provider: Optional[str] = None
    stream: Optional[bool] = False
    tools: Optional[List[dict]] = None
    tool_choice: Optional[OpenAIToolChoice] = Field(
        default=None,
        description="Backend-conditional tool selection; unsupported Gemini values return HTTP 400.",
    )
    max_tokens: Optional[StrictInt] = Field(
        default=None,
        gt=0,
        description="Positive output-token limit; accepted for compatibility but not currently enforced by Gemini backends.",
    )
    max_completion_tokens: Optional[StrictInt] = Field(
        default=None,
        gt=0,
        description="Positive output-token limit alias; accepted for compatibility but not currently enforced by Gemini backends.",
    )
    temperature: Optional[StrictFloat] = Field(
        default=None,
        ge=0,
        le=2,
        description="Sampling temperature in the inclusive range 0 through 2; backend support is conditional.",
    )
    top_p: Optional[StrictFloat] = Field(
        default=None,
        ge=0,
        le=1,
        description="Nucleus sampling probability in the inclusive range 0 through 1; backend support is conditional.",
    )
    top_k: Optional[StrictInt] = Field(
        default=None,
        ge=1,
        description="Positive top-k sampling value; backend support is conditional.",
    )
    reasoning_effort: Optional[
        Literal["none", "minimal", "low", "medium", "high", "xhigh"]
    ] = Field(
        default=None,
        description="OpenAI-style reasoning effort; current Gemini backends accept it for compatibility without applying it.",
    )
    stream_options: Optional[OpenAIStreamOptions] = Field(
        default=None,
        description="Streaming options. Only include_usage is currently part of the compatibility contract.",
    )
    response_format: Optional[OpenAIResponseFormat] = Field(
        default=None,
        description="OpenAI response format shape; current Gemini backends do not implement structured output semantics.",
    )
    parallel_tool_calls: Optional[StrictBool] = Field(
        default=None,
        description="Whether multiple tool calls may be emitted in parallel; backend-conditional.",
    )
    gem: Optional[str] = Field(default=None, description="Gem ID or name to use as system prompt.")
    conversation_id: Optional[str] = Field(default=None, description="ID to continue an existing browser conversation.")
    provider_options: Optional[ProviderOptions] = None

    @field_validator("tools", mode="before")
    @classmethod
    def validate_tool_declarations(cls, tools: Any) -> Any:
        return validate_openai_tool_declarations(tools)

    @model_validator(mode="after")
    def validate_token_aliases(self) -> Self:
        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError(
                "max_tokens and max_completion_tokens are mutually exclusive; provide only one."
            )
        return self

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
