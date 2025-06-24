# src/app/endpoints/chat.py
import time
from fastapi import APIRouter, HTTPException
from app.logger import logger
from schemas.request import GeminiRequest, OpenAIChatRequest
from app.services.gemini_client import get_gemini_client
from app.services.session_manager import get_translate_session_manager

router = APIRouter()

@router.post("/translate")
async def translate_chat(request: GeminiRequest):
    gemini_client = get_gemini_client()
    session_manager = get_translate_session_manager()
    if not gemini_client or not session_manager:
        raise HTTPException(status_code=503, detail="Gemini client is not initialized.")
    try:
        # This call now correctly uses the fixed session manager
        response = await session_manager.get_response(request.model, request.message, request.images)
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /translate endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during translation: {str(e)}")

def convert_to_openai_format(response_text: str, model: str, stream: bool = False):
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk" if stream else "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

@router.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatRequest):
    is_stream = request.stream if request.stream is not None else False
    gemini_client = get_gemini_client()
    if not gemini_client:
        raise HTTPException(status_code=503, detail="Gemini client is not initialized.")
    
    # Extract the user message from the list of messages
    user_message = next((msg.get("content") for msg in request.messages if msg.get("role") == "user"), None)
    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found.")
    
    if request.model:
        try:
            # FIX: The underlying `generate_content` call needs to be adapted.
            # This assumes `MyGeminiClient.generate_content` is also updated to use `prompt`.
            # We pass `images=None` as this endpoint doesn't handle images.
            response = await gemini_client.generate_content(message=user_message, model=request.model.value, images=None)
            return convert_to_openai_format(response.text, request.model.value, is_stream)
        except Exception as e:
            logger.error(f"Error in /v1/chat/completions endpoint: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error processing chat completion: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail="Model not specified in the request.")
