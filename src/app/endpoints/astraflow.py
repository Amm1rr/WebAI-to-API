# src/app/endpoints/astraflow.py
"""
FastAPI router for the Astraflow provider.

Exposes two endpoints that mirror the OpenAI API surface:
  GET  /astraflow/v1/models
  POST /astraflow/v1/chat/completions

The router simply proxies requests to the Astraflow REST API via
`app.services.astraflow_client`, adding authentication transparently.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.logger import logger
from app.services.astraflow_client import (
    AstraflowClientNotConfiguredError,
    _default_model,
    chat_completions,
    chat_completions_stream,
    list_models,
)
from schemas.request import OpenAIChatRequest

router = APIRouter(prefix="/astraflow")


@router.get("/v1/models", summary="List Astraflow models")
async def astraflow_list_models():
    """
    Returns the list of models available through the Astraflow endpoint.
    Proxies GET /v1/models from the upstream Astraflow API.
    """
    try:
        return await list_models()
    except AstraflowClientNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[Astraflow] Error listing models: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error listing Astraflow models: {str(e)}")


@router.post("/v1/chat/completions", summary="Astraflow chat completions")
async def astraflow_chat_completions(request: OpenAIChatRequest):
    """
    OpenAI-compatible chat completions endpoint backed by Astraflow.

    Accepts the same request schema as POST /v1/chat/completions and
    transparently proxies it to the Astraflow API, including SSE streaming.
    """
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")

    model = request.model or _default_model()

    payload = {
        "model": model,
        "messages": request.messages,
        "stream": bool(request.stream),
    }
    if request.tools:
        payload["tools"] = request.tools
    if request.tool_choice is not None:
        payload["tool_choice"] = request.tool_choice

    try:
        if request.stream:
            return StreamingResponse(
                chat_completions_stream(payload),
                media_type="text/event-stream",
            )
        return await chat_completions(payload)
    except AstraflowClientNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[Astraflow] Error in chat completions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing Astraflow chat completion: {str(e)}")
