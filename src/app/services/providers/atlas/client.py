import os
from typing import Any, Optional

import httpx

from app.logger import logger


class AtlasClientNotConfiguredError(Exception):
    """Raised when Atlas Cloud configuration is missing."""


class AtlasClientError(Exception):
    """Raised when Atlas Cloud returns an invalid response."""


class AtlasClient:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        # Ensure base_url ends with a slash for httpx relative path resolution
        self.base_url = base_url.rstrip("/") + "/"

    async def chat_completions(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        stream: bool = False,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> httpx.Response:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        timeout = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)
        client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        request = client.build_request("POST", "chat/completions", json=payload)
        response = await client.send(request, stream=stream)

        if response.is_error:
            details = await response.aread()
            await response.aclose()
            await client.aclose()
            detail_text = details.decode("utf-8", errors="replace")
            logger.error("Atlas Cloud API request failed: %s", detail_text)
            raise AtlasClientError(
                f"Atlas Cloud API returned {response.status_code}: {detail_text}"
            )

        # Attach client to response so it can be closed after streaming
        response._atlas_client = client  # type: ignore[attr-defined]
        return response

    async def list_models(self) -> list[dict[str, Any]]:
        """Fetch the live model list from Atlas Cloud."""
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        ) as client:
            response = await client.get("models")
            if response.is_error:
                detail_text = response.text
                logger.error("Atlas Cloud model discovery failed: %s", detail_text)
                raise AtlasClientError(
                    f"Atlas Cloud API returned {response.status_code} for /models: {detail_text}"
                )
            
            data = response.json()
            # Atlas returns { "code": 200, "msg": "succeed", "data": [...] }
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                return data["data"]
            
            logger.error("Atlas Cloud model discovery returned invalid format: %s", data)
            raise AtlasClientError("Atlas Cloud API returned invalid model list format")


def get_atlas_client() -> AtlasClient:
    from app.config import CONFIG

    api_key = os.getenv("ATLASCLOUD_API_KEY") or CONFIG.get("Atlas", "api_key", fallback=None)
    if not api_key:
        raise AtlasClientNotConfiguredError(
            "Atlas Cloud API key not configured. Set ATLASCLOUD_API_KEY in .env.local or [Atlas] api_key in config.conf."
        )

    base_url = (
        os.getenv("ATLASCLOUD_BASE_URL")
        or CONFIG.get("Atlas", "base_url", fallback="https://api.atlascloud.ai/v1")
    )
    return AtlasClient(api_key=api_key, base_url=base_url)
