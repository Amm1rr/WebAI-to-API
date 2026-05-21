# src/app/services/astraflow_client.py
"""
Thin async client for the Astraflow OpenAI-compatible REST API.

Astraflow (by UCloud / 优刻得) exposes an OpenAI-compatible interface, so no
special SDK is required — only a base_url switch and an API key.

Endpoints:
  Global : https://api-us-ca.umodelverse.ai/v1   (env: ASTRAFLOW_API_KEY)
  China  : https://api.modelverse.cn/v1          (env: ASTRAFLOW_CN_API_KEY)
"""

import os
import logging
from typing import AsyncIterator, List, Optional

import httpx

from app.config import CONFIG

logger = logging.getLogger(__name__)

_GLOBAL_BASE_URL = "https://api-us-ca.umodelverse.ai/v1"
_CN_BASE_URL = "https://api.modelverse.cn/v1"


class AstraflowClientNotConfiguredError(Exception):
    """Raised when no API key is available for Astraflow."""


def _get_base_url() -> str:
    use_cn = CONFIG.get("Astraflow", "use_cn_endpoint", fallback="false").strip().lower()
    return _CN_BASE_URL if use_cn == "true" else _GLOBAL_BASE_URL


def _get_api_key() -> str:
    """
    Resolve the API key with the following priority:
      1. Environment variable (ASTRAFLOW_CN_API_KEY / ASTRAFLOW_API_KEY)
      2. config.conf [Astraflow] section
    """
    use_cn = CONFIG.get("Astraflow", "use_cn_endpoint", fallback="false").strip().lower()
    if use_cn == "true":
        key = os.environ.get("ASTRAFLOW_CN_API_KEY") or CONFIG.get("Astraflow", "cn_api_key", fallback="")
    else:
        key = os.environ.get("ASTRAFLOW_API_KEY") or CONFIG.get("Astraflow", "api_key", fallback="")
    if not key:
        raise AstraflowClientNotConfiguredError(
            "Astraflow API key not set. Provide ASTRAFLOW_API_KEY (or ASTRAFLOW_CN_API_KEY) "
            "as an environment variable or set api_key in the [Astraflow] section of config.conf."
        )
    return key


def _default_model() -> str:
    return CONFIG.get("Astraflow", "default_model", fallback="gpt-4o")


async def list_models() -> dict:
    """Return the list of models available on the Astraflow endpoint."""
    api_key = _get_api_key()
    base_url = _get_base_url()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        return resp.json()


async def chat_completions(payload: dict) -> dict:
    """Non-streaming chat completion — returns the full response dict."""
    api_key = _get_api_key()
    base_url = _get_base_url()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def chat_completions_stream(payload: dict) -> AsyncIterator[bytes]:
    """Streaming chat completion — yields raw SSE bytes from the upstream."""
    api_key = _get_api_key()
    base_url = _get_base_url()
    stream_payload = {**payload, "stream": True}
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=stream_payload,
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk
