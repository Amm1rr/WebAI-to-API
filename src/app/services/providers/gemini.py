import asyncio
import json
import time
from typing import Any, List, Optional
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from gemini_webapi.exceptions import APIError
from app.services.base import BaseProvider
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.providers.base_repository import ProviderCapability
from app.services.providers.exceptions import SessionRecoveryError, SnapshotNotFoundError, StateIntegrityError
from app.services.session_manager import get_translate_session_manager, get_gemini_chat_registry, generate_opaque_token
from app.utils.streaming import simulate_streaming_generator
from app.logger import logger
from app.schemas.request import OpenAIChatRequest
from models.gemini import resolve_model_name

def is_unknown_model_error(error: ValueError) -> bool:
    return "Unknown model name" in str(error)

class GeminiProvider(BaseProvider):
    """
    Browser/session-driven provider for Google Gemini.
    Handles stateful sessions, cookie rotation, and prompt-based tool-calling simulation.
    """
    provider_name = "gemini"
    capabilities = {ProviderCapability.PERSISTENT_RECOVERY}

    async def chat_completions(self, request: OpenAIChatRequest) -> Any:
        try:
            gemini_client = get_gemini_client()
        except GeminiClientNotInitializedError as e:
            raise HTTPException(status_code=503, detail=str(e))

        if not request.messages:
            raise HTTPException(status_code=400, detail="No messages provided.")

        self._validate_model_name(request.model)
        self._require_authenticated_conversation_recovery(request.conversation_id, gemini_client)

        # 1. Resolve or generate conversation_id securely
        cid = request.conversation_id
        is_new_conversation = cid is None
        if cid:
            if len(cid) > 64:
                raise HTTPException(status_code=400, detail="Invalid conversation_id length.")
        else:
            cid = generate_opaque_token()

        # 2. Retrieve stateful SessionManager from SessionRegistry
        registry = get_gemini_chat_registry()
        if not registry:
            raise HTTPException(status_code=503, detail="Session registry is not initialized.")
        
        try:
            session_manager = await registry.get_session(
                cid,
                self,
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

        # 3. Build tool-calling prompt
        tools_prompt = self._build_tools_prompt(request.tools) if request.tools else ""
        is_stream = request.stream if request.stream is not None else False

        try:
            # 4. Progressive Streaming Path (only if no tools are used)
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
                                openai_chunk = self._convert_to_openai_format(
                                    chunk["text_delta"],
                                    request.model or "unknown",
                                    stream=True
                                )
                                openai_chunk["conversation_id"] = cid
                                openai_chunk["reused_conversation"] = chunk.get("is_reused", False)
                                yield await format_sse_chunk(openai_chunk)
                    except (asyncio.CancelledError, GeneratorExit):
                        # Client disconnected or generator closed, propagate to stop upstream
                        raise
                    except Exception as e:
                        logger.error(f"Error in Gemini progressive streaming: {e}", exc_info=True)
                    else:
                        await registry.save_session_snapshot(cid, self, session_manager)
                        # Only send [DONE] if the stream finished successfully
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

            # 5. Buffered Path (for non-streaming or tool-calling)
            response, is_reused = await session_manager.get_response_stateful(
                model=request.model,
                messages=request.messages,
                tools_prompt=tools_prompt,
                files=None,
                gem=request.gem
            )
            await registry.save_session_snapshot(cid, self, session_manager)
            
            # 6. Parse tool calls if necessary
            tool_call = self._parse_tool_call(response.text) if request.tools else None
            
            # 7. Normalize response to OpenAI format
            openai_response = self._convert_to_openai_format(
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
            if request.conversation_id and self._is_unrecoverable_conversation_error(e):
                raise HTTPException(
                    status_code=410,
                    detail="The provided conversation_id can no longer be recovered. Start a new conversation.",
                ) from e
            logger.error(f"Error in GeminiProvider.chat_completions: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error processing Gemini chat completion: {str(e)}")
        except Exception as e:
            logger.error(f"Error in GeminiProvider.chat_completions: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error processing Gemini chat completion: {str(e)}")

    def _validate_model_name(self, model: Optional[str]) -> None:
        if not model:
            return

        from gemini_webapi.constants import Model

        try:
            Model.from_name(resolve_model_name(model))
        except ValueError as e:
            if is_unknown_model_error(e):
                raise HTTPException(status_code=400, detail=str(e)) from e
            raise

    def _require_authenticated_conversation_recovery(self, conversation_id: Optional[str], gemini_client: Any) -> None:
        if not conversation_id:
            return

        client = getattr(gemini_client, "client", None)
        account_status = getattr(client, "account_status", None)
        status_name = getattr(account_status, "name", None)

        if status_name != "AVAILABLE":
            raise HTTPException(
                status_code=401,
                detail="The provided conversation_id requires an authenticated Gemini session. Please sign in and try again.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def _is_unrecoverable_conversation_error(self, error: APIError) -> bool:
        error_code = (
            getattr(error, "code", None)
            or getattr(error, "error_code", None)
            or getattr(error, "status_code", None)
        )
        if error_code is not None:
            return str(error_code) == "1097"
        return "1097" in str(error)

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


    async def list_models(self) -> List[dict]:
        from gemini_webapi.constants import Model
        ts = int(time.time())
        return [
            {
                "id": model.model_name,
                "object": "model",
                "created": ts,
                "owned_by": "google",
            }
            for model in Model
            if model != Model.UNSPECIFIED
        ]

    async def close(self) -> None:
        # Gemini client is managed globally in gemini_client.py
        pass

    def _build_tools_prompt(self, tools: list) -> str:
        """Convert OpenAI tool definitions to a system prompt for Gemini."""
        declarations = []
        for t in tools:
            if t.get("type") == "function" and "function" in t:
                declarations.append(t["function"])
        if not declarations:
            return ""
        lines = [
            "You have access to the following tools. When you want to call a tool, respond with "
            "ONLY a JSON object in this exact format, with no other text before or after:\n"
            '{"tool_call": {"name": "<tool_name>", "arguments": {<arguments>}}}\n',
            "Available tools:",
        ]
        for fn in declarations:
            lines.append(f"- {fn['name']}: {fn.get('description', '')}")
            if fn.get("parameters"):
                lines.append(f"  Parameters: {json.dumps(fn['parameters'])}")
        return "\n".join(lines)

    def _parse_tool_call(self, text: str) -> Optional[dict]:
        """Extract a tool_call JSON object from model response text."""
        decoder = json.JSONDecoder()
        for i, ch in enumerate(text):
            if ch == '{':
                try:
                    obj, _ = decoder.raw_decode(text, i)
                    if isinstance(obj, dict) and "tool_call" in obj:
                        return obj["tool_call"]
                except (json.JSONDecodeError, ValueError):
                    pass
        return None


    def _convert_to_openai_format(self, response_text: str, model: str, stream: bool = False, tool_call: Optional[dict] = None):
        ts = int(time.time())
        choice_key = "delta" if stream else "message"
        
        if tool_call:
            args = tool_call.get("arguments", {})
            content = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call_{ts}",
                    "type": "function",
                    "function": {
                        "name": tool_call.get("name", ""),
                        "arguments": json.dumps(args) if isinstance(args, dict) else args,
                    },
                }],
            }
            return {
                "id": f"chatcmpl-{ts}",
                "object": "chat.completion.chunk" if stream else "chat.completion",
                "created": ts,
                "model": model,
                "choices": [{
                    "index": 0,
                    choice_key: content,
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        return {
            "id": f"chatcmpl-{ts}",
            "object": "chat.completion.chunk" if stream else "chat.completion",
            "created": ts,
            "model": model,
            "choices": [{
                "index": 0,
                choice_key: {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


def serialize_session_state(session: Any) -> str:
    """
    Serializes a Gemini ChatSession's private state into a JSON string.
    """
    if session is None:
        raise ValueError("Session cannot be None.")

    gem_id = None
    if session.gem:
        gem_id = session.gem.id if hasattr(session.gem, 'id') else session.gem

    model_name = ""
    if session.model:
        if isinstance(session.model, str):
            model_name = session.model
        elif isinstance(session.model, dict):
            model_name = session.model.get("model_name", "")
        elif hasattr(session.model, "model_name"):
            model_name = session.model.model_name
        else:
            model_name = str(session.model)

    payload = {
        "provider_state_version": 1,
        "metadata": session.metadata,
        "gem_id": gem_id,
        "model_name": model_name
    }
    return json.dumps(payload)


def validate_session_state_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise StateIntegrityError("Session state must be a JSON object.")

    required_keys = {"provider_state_version", "metadata", "gem_id", "model_name"}
    missing_keys = required_keys - payload.keys()
    if missing_keys:
        raise StateIntegrityError(f"Missing required session state fields: {', '.join(sorted(missing_keys))}")

    version = payload.get("provider_state_version")
    if version != 1:
        raise StateIntegrityError(f"Unsupported provider state version: {version}")

    metadata = payload.get("metadata")
    if not isinstance(metadata, list) or len(metadata) < 3:
        raise StateIntegrityError("Missing or invalid metadata context in session state.")
    if not all(isinstance(value, str) and value for value in metadata[:3]):
        raise StateIntegrityError("Missing or invalid Gemini continuation metadata fields.")


def deserialize_session_state(
    state_str: str,
    client: Any,
    *,
    model: Optional[Any] = None,
    gem: Optional[Any] = None,
) -> Any:
    """
    Deserializes a Gemini ChatSession's state from a JSON string,
    recreates the session using the client, and safely isolates the metadata reference.
    """
    try:
        payload = json.loads(state_str)
    except Exception as e:
        raise StateIntegrityError(f"Malformed JSON state: {e}")

    validate_session_state_payload(payload)

    metadata = payload.get("metadata")
    model_name = model if model is not None else payload.get("model_name")
    gem_id = gem if gem is not None else payload.get("gem_id")

    # Start a clean chat session
    session = client.start_chat(model=model_name, gem=gem_id)

    # Guarantee isolated metadata copy to break shared reference to DEFAULT_METADATA
    session._ChatSession__metadata = list(metadata)

    return session
