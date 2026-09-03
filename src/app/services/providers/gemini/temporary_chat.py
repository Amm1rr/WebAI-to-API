"""Deprecated compatibility wrapper for /v1/temporary/chat/completions.

Canonical implementation lives in :mod:`app.services.providers.gemini.stateless_chat`.
This module is a thin shim that delegates to the canonical stateless
implementation. It preserves historical import paths via aliases.
"""

from app.services.providers.gemini.stateless_chat import (  # noqa: F401
    DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS,
    StatelessChatRequestContext,
    _build_buffered_openai_response,
    _build_cleanup_once,
    _build_incremental_streaming_response,
    _build_streaming_compatibility_response,
    _ensure_direct_webapi_ready,
    _prepare_stateless_chat_request,
    _resolve_stateless_chat_model,
    _streaming_headers,
    _translate_direct_gemini_error,
    _validate_stateless_chat_request,
    handle_stateless_chat_completions,
)

# Compatibility aliases for historical import paths.
TemporaryChatRequestContext = StatelessChatRequestContext


def _validate_temporary_chat_request(request):
    return _validate_stateless_chat_request(
        request,
        endpoint_name="temporary",
        direct_webapi_only=False,
    )


def _resolve_temporary_chat_model(request, gemini_client):
    return _resolve_stateless_chat_model(
        request,
        gemini_client,
        endpoint_name="temporary",
        direct_webapi_only=False,
    )


def _prepare_temporary_chat_request(request, gemini_client):
    return _prepare_stateless_chat_request(
        request,
        gemini_client,
        endpoint_name="temporary",
        direct_webapi_only=False,
    )


async def handle_temporary_chat_completions(request, *, endpoint_name: str = "temporary", direct_webapi_only: bool = False):
    """Deprecated wrapper: delegates to canonical stateless implementation.

    Preserves legacy ``gemini/<model>`` normalization via
    ``direct_webapi_only=False``.
    """
    return await handle_stateless_chat_completions(
        request, endpoint_name=endpoint_name, direct_webapi_only=direct_webapi_only
    )


__all__ = [
    "DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS",
    "TemporaryChatRequestContext",
    "StatelessChatRequestContext",
    "handle_temporary_chat_completions",
    "handle_stateless_chat_completions",
    "_validate_temporary_chat_request",
    "_resolve_temporary_chat_model",
    "_prepare_temporary_chat_request",
]
