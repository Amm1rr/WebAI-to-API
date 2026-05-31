import time
import json
from typing import Any, List, Optional
from fastapi import HTTPException

from app.services.base import BaseProvider
from app.services.providers.base_repository import ProviderCapability
from app.services.providers.gemini.persistence import (
    serialize_session_state,
    deserialize_session_state,
    validate_session_state_payload,
)
from app.utils.tokens import generate_opaque_token
from app.schemas.request import OpenAIChatRequest
from app.config import CONFIG

from app.services.providers.gemini.shared import validate_model_name, build_tools_prompt
from app.services.providers.gemini.webapi_adapter import GeminiWebAPIAdapter
from app.services.providers.gemini.playwright_adapter import GeminiPlaywrightAdapter

class GeminiProvider(BaseProvider):
    """
    Unified logical provider for Google Gemini.
    Coordinates multiple internal execution strategies (WebAPI and Playwright).
    """
    provider_name = "gemini"
    capabilities = {ProviderCapability.PERSISTENT_RECOVERY}

    def __init__(self):
        self.webapi_adapter = GeminiWebAPIAdapter(self)
        self.playwright_adapter = GeminiPlaywrightAdapter(self)
        
        # Determine default backend from configuration
        self.default_backend = CONFIG["Gemini"].get("backend", "webapi").lower()

    def _get_adapter(self, model: str) -> Any:
        """Select the appropriate adapter based on model name or configuration."""
        if model and model.startswith("playwright/"):
            return self.playwright_adapter
        
        if self.default_backend == "playwright":
            return self.playwright_adapter
        
        return self.webapi_adapter

    async def chat_completions(self, request: OpenAIChatRequest) -> Any:
        if not request.messages:
            raise HTTPException(status_code=400, detail="No messages provided.")

        validate_model_name(request.model)

        # 1. Resolve or generate conversation_id securely
        cid = request.conversation_id
        is_new_conversation = cid is None
        if cid:
            if len(cid) > 64:
                raise HTTPException(status_code=400, detail="Invalid conversation_id length.")
        else:
            cid = generate_opaque_token()

        # 2. Build tool-calling prompt
        tools_prompt = build_tools_prompt(request.tools) if request.tools else ""
        
        # 3. Select adapter and delegate
        adapter = self._get_adapter(request.model)
        return await adapter.chat_completions(request, cid, is_new_conversation, tools_prompt)

    async def list_models(self) -> List[dict]:
        from app.services.providers.gemini.shared import get_gemini_models
        return get_gemini_models()

    async def close(self) -> None:
        await self.webapi_adapter.close()
        await self.playwright_adapter.close()

    # Shared delegation for persistence (called by registries)
    def serialize_session_state(self, session: Any) -> dict:
        return json.loads(serialize_session_state(session))

    def deserialize_session_state(self, session_state: dict, client: Any, **kwargs) -> Any:
        return deserialize_session_state(json.dumps(session_state), client, **kwargs)

    def validate_session_recovery(self, session_state: dict, client_context: Optional[dict] = None) -> dict:
        validate_session_state_payload(session_state)
        return session_state

    # Backward compatibility for tests
    def _parse_tool_call(self, text: str) -> Optional[dict]:
        from app.services.providers.gemini.shared import parse_tool_call
        return parse_tool_call(text)

    def _convert_to_openai_format(self, text: str, model: str, stream: bool = False, tool_call: Optional[dict] = None):
        from app.services.providers.gemini.shared import convert_to_openai_format
        return convert_to_openai_format(text, model, stream, tool_call)

    def _build_tools_prompt(self, tools: list) -> str:
        from app.services.providers.gemini.shared import build_tools_prompt
        return build_tools_prompt(tools)
