import asyncio
import json
from typing import Any, List, Optional
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from gemini_webapi.exceptions import APIError

from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.providers.gemini.session_manager import get_gemini_chat_registry
from app.services.providers.exceptions import SessionRecoveryError, SnapshotNotFoundError
from app.services.providers.gemini.base_adapter import GeminiBackendAdapter
from app.services.providers.gemini.shared import (
    convert_to_openai_format, 
    parse_tool_call,
    UNRECOVERABLE_CONVERSATION_ERROR_CODES
)
from app.services.providers.gemini.persistence import (
    serialize_session_state,
    deserialize_session_state,
    validate_session_state_payload
)
from app.logger import logger
from app.schemas.request import OpenAIChatRequest

class GeminiWebAPIAdapter(GeminiBackendAdapter):
    """
    Backend adapter for Google Gemini using the gemini-webapi library.
    Handles stateful sessions via SessionRegistry and SQLite.
    """

    def __init__(self, provider):
        self.provider = provider

    async def chat_completions(self, request: OpenAIChatRequest, cid: str, is_new_conversation: bool, tools_prompt: str) -> Any:
        try:
            gemini_client = get_gemini_client()
        except GeminiClientNotInitializedError as e:
            raise HTTPException(status_code=503, detail=str(e))

        # Check client authentication status
        account_status = getattr(gemini_client.client, "account_status", None)
        status_name = getattr(account_status, "name", "UNKNOWN") if account_status else "UNKNOWN"
        
        if status_name != "AVAILABLE":
            logger.warning(f"Gemini client account status is '{status_name}'.")
            if status_name == "UNAUTHENTICATED":
                raise HTTPException(
                    status_code=401,
                    detail="The provided conversation_id requires an authenticated Gemini session. Please sign in and try again.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            raise HTTPException(
                status_code=401 if status_name == "UNKNOWN" else 503,
                detail=f"Gemini client is not ready (status: {status_name}).",
            )

        # 1. Retrieve stateful SessionManager from SessionRegistry
        registry = get_gemini_chat_registry()
        if not registry:
            raise HTTPException(status_code=503, detail="Session registry is not initialized.")
        
        try:
            session_manager = await registry.get_session(
                cid,
                self.provider,
                allow_create=is_new_conversation,
                model=request.model,
                gem=request.gem,
            )
        except SnapshotNotFoundError:
            raise HTTPException(
                status_code=404,
                detail="The provided conversation_id was not found.",
            )
        except SessionRecoveryError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

        is_stream = request.stream if request.stream is not None else False

        try:
            # 2. Progressive Streaming Path (only if no tools are used)
            if is_stream and not request.tools:
                async def sse_generator():
                    from app.utils.streaming import format_sse_chunk, get_done_chunk
                    try:
                        async for chunk in session_manager.get_streaming_response_stateful(
                            model=request.model,
                            messages=request.messages,
                            tools_prompt=tools_prompt,
                            files=None,
                            gem=request.gem
                        ):
                            if chunk.get("type") == "chunk" and chunk.get("text_delta"):
                                openai_chunk = convert_to_openai_format(
                                    chunk["text_delta"],
                                    request.model or "unknown",
                                    stream=True
                                )
                                openai_chunk["conversation_id"] = cid
                                openai_chunk["reused_conversation"] = chunk.get("is_reused", False)
                                yield await format_sse_chunk(openai_chunk)
                    except (asyncio.CancelledError, GeneratorExit):
                        raise
                    except Exception as e:
                        logger.error(f"Error in Gemini WebAPI progressive streaming: {e}", exc_info=True)
                    else:
                        await registry.save_session_snapshot(cid, self.provider, session_manager)
                        yield await get_done_chunk()

                return StreamingResponse(
                    sse_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    }
                )

            # 3. Buffered Path (for non-streaming or tool-calling)
            response, is_reused = await session_manager.get_response_stateful(
                model=request.model,
                messages=request.messages,
                tools_prompt=tools_prompt,
                files=None,
                gem=request.gem
            )
            await registry.save_session_snapshot(cid, self.provider, session_manager)
            
            # 4. Parse tool calls if necessary
            tool_call = parse_tool_call(response.text) if request.tools else None
            
            # 5. Normalize response to OpenAI format
            openai_response = convert_to_openai_format(
                response.text, 
                request.model or "unknown", 
                is_stream, 
                tool_call
            )
            
            # Inject stateful conversation identifiers
            openai_response["conversation_id"] = cid
            openai_response["reused_conversation"] = is_reused
            
            if is_stream:
                from app.utils.streaming import simulate_streaming_generator
                return StreamingResponse(
                    simulate_streaming_generator(openai_response), 
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    }
                )
                
            return openai_response

        except HTTPException:
            raise
        except APIError as e:
            if not is_new_conversation and self._is_unrecoverable_conversation_error(e):
                raise HTTPException(
                    status_code=410,
                    detail="The provided conversation_id can no longer be recovered. Start a new conversation.",
                ) from e
            logger.error(f"Error in GeminiWebAPIAdapter.chat_completions: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error processing Gemini chat completion: {str(e)}")
        except Exception as e:
            logger.error(f"Error in GeminiWebAPIAdapter.chat_completions: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error processing Gemini chat completion: {str(e)}")

    def _is_unrecoverable_conversation_error(self, error: APIError) -> bool:
        error_code = (
            getattr(error, "code", None)
            or getattr(error, "error_code", None)
            or getattr(error, "status_code", None)
        )
        if error_code is not None:
            return str(error_code) in UNRECOVERABLE_CONVERSATION_ERROR_CODES
        return any(code in str(error) for code in UNRECOVERABLE_CONVERSATION_ERROR_CODES)

    async def close(self) -> None:
        pass

    def serialize_session_state(self, session: Any) -> dict:
        return json.loads(serialize_session_state(session))

    def deserialize_session_state(
        self,
        session_state: dict,
        client: Any,
        *,
        model: Optional[Any] = None,
        gem: Optional[Any] = None,
    ) -> Any:
        return deserialize_session_state(
            json.dumps(session_state),
            client,
            model=model,
            gem=gem,
        )

    def validate_session_recovery(self, session_state: dict, client_context: Optional[dict] = None) -> dict:
        validate_session_state_payload(session_state)
        return session_state
