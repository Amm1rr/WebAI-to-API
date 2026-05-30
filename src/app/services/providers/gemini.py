import asyncio
import json
import time
from typing import Any, List, Optional
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from app.services.base import BaseProvider
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.session_manager import get_translate_session_manager, get_gemini_chat_registry, generate_opaque_token
from app.utils.streaming import simulate_streaming_generator
from app.logger import logger
from app.schemas.request import OpenAIChatRequest

class GeminiProvider(BaseProvider):
    """
    Browser/session-driven provider for Google Gemini.
    Handles stateful sessions, cookie rotation, and prompt-based tool-calling simulation.
    """

    async def chat_completions(self, request: OpenAIChatRequest) -> Any:
        try:
            gemini_client = get_gemini_client()
        except GeminiClientNotInitializedError as e:
            raise HTTPException(status_code=503, detail=str(e))

        if not request.messages:
            raise HTTPException(status_code=400, detail="No messages provided.")

        # 1. Resolve or generate conversation_id securely
        cid = request.conversation_id
        if cid:
            if len(cid) > 64:
                raise HTTPException(status_code=400, detail="Invalid conversation_id length.")
        else:
            cid = generate_opaque_token()

        # 2. Retrieve stateful SessionManager from SessionRegistry
        registry = get_gemini_chat_registry()
        if not registry:
            raise HTTPException(status_code=503, detail="Session registry is not initialized.")
        
        session_manager = await registry.get_session(cid)

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

        except Exception as e:
            logger.error(f"Error in GeminiProvider.chat_completions: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error processing Gemini chat completion: {str(e)}")


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
