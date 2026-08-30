from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from fastapi import HTTPException

from app.schemas.request import OpenAIChatRequest


class OpenAIRequestCapability(str, Enum):
    SUPPORTED = "supported"
    ACCEPTED_NO_EFFECT = "accepted_no_effect"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class OpenAICompatibilityCapabilities:
    """Backend-owned compatibility status for request-level OpenAI controls."""

    backend_name: str
    fields: Mapping[str, OpenAIRequestCapability]

    def status(self, field_name: str) -> OpenAIRequestCapability:
        return self.fields.get(field_name, OpenAIRequestCapability.UNSUPPORTED)


OPENAI_COMPATIBILITY_FIELDS = (
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "top_k",
    "reasoning_effort",
    "stream_options",
    "response_format",
    "parallel_tool_calls",
    "tool_choice",
)


DEFAULT_OPENAI_COMPATIBILITY_CAPABILITIES = OpenAICompatibilityCapabilities(
    backend_name="provider",
    fields={},
)


def validate_openai_request_compatibility(
    request: OpenAIChatRequest,
    capabilities: OpenAICompatibilityCapabilities,
) -> None:
    """Reject explicitly supplied controls the selected backend cannot honor."""
    unsupported_fields = sorted(
        field_name
        for field_name in OPENAI_COMPATIBILITY_FIELDS
        if getattr(request, field_name, None) is not None
        and capabilities.status(field_name) is OpenAIRequestCapability.UNSUPPORTED
    )
    if unsupported_fields:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported fields for {capabilities.backend_name}: "
                f"{', '.join(unsupported_fields)}."
            ),
        )
