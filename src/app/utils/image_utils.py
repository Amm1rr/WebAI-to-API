# src/app/utils/image_utils.py
"""
Utilities for image processing: base64 decoding, URL downloading,
Gemini response serialization, and temp file management.
"""

import base64
import hashlib
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx

from app.logger import logger

# ---------------------------------------------------------------------------
# Temp directory — created once per process lifetime
# ---------------------------------------------------------------------------
_TEMP_DIR: Path = Path(tempfile.mkdtemp(prefix="webai_uploads_"))

_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "application/pdf": ".pdf",
}

ALLOWED_MIME_TYPES: set[str] = set(_MIME_TO_EXT.keys())


def get_temp_dir() -> Path:
    """Return the shared temp directory for this process."""
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return _TEMP_DIR


def _unique_name(prefix: str, ext: str) -> str:
    ts = int(time.time() * 1000)
    return f"{prefix}_{ts}{ext}"


# ---------------------------------------------------------------------------
# Decode base64 data URI → temp file
# ---------------------------------------------------------------------------
def decode_base64_to_tempfile(data_uri: str) -> Path:
    """
    Decode a base64 data URI (``data:<mime>;base64,<data>``) to a temp file.

    Returns the Path of the saved file.
    Raises ValueError for invalid format.
    """
    match = re.match(r"data:([^;]+);base64,(.+)", data_uri, re.DOTALL)
    if not match:
        raise ValueError(f"Invalid data URI: {data_uri[:60]}…")

    mime_type = match.group(1).strip()
    b64_data = match.group(2).strip()
    ext = _MIME_TO_EXT.get(mime_type, ".bin")

    raw = base64.b64decode(b64_data)
    dest = get_temp_dir() / _unique_name("b64", ext)
    dest.write_bytes(raw)
    logger.debug(f"Decoded base64 → {dest} ({len(raw)} bytes)")
    return dest


# ---------------------------------------------------------------------------
# Download URL → temp file
# ---------------------------------------------------------------------------
async def download_to_tempfile(url: str, cookies: Optional[dict] = None) -> Optional[Path]:
    """
    Download an image/file from *url* into a temp file.

    ``cookies`` is forwarded for authenticated Gemini URLs (generated images).
    Returns the Path on success, None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=30, cookies=cookies or {}) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            ext = _MIME_TO_EXT.get(content_type, ".jpg")
            dest = get_temp_dir() / _unique_name("dl", ext)
            dest.write_bytes(resp.content)
            logger.debug(f"Downloaded {url} → {dest} ({len(resp.content)} bytes)")
            return dest
    except Exception as exc:
        logger.warning(f"Failed to download {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Fetch image URL → base64 data URI string
# ---------------------------------------------------------------------------
async def fetch_image_as_base64(url: str, cookies: Optional[dict] = None) -> str:
    """
    Download an image from *url* and return it as a ``data:<mime>;base64,<data>`` string.

    Returns empty string on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=30, cookies=cookies or {}) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "image/png").split(";")[0].strip()
            b64 = base64.b64encode(resp.content).decode()
            return f"data:{content_type};base64,{b64}"
    except Exception as exc:
        logger.warning(f"Failed to fetch image as base64 from {url}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Serialize GeminiResponse images → list[dict]
# ---------------------------------------------------------------------------
async def serialize_response_images(response, gemini_cookies: Optional[dict] = None) -> list[dict]:
    """
    Extract all images from a *GeminiResponse* (ModelOutput) and return a list
    of dicts suitable for JSON serialization.

    Each dict has:
      - type: "web_image" | "generated_image"
      - url: original Gemini URL
      - base64: data URI (downloaded with auth cookies if needed), empty on failure
      - title: image title
      - alt: alt text / description
    """
    if not response.candidates:
        return []

    chosen = response.candidates[response.chosen]
    result: list[dict] = []

    # Web images — publicly accessible URLs
    for img in chosen.web_images:
        b64 = await fetch_image_as_base64(img.url)
        result.append({
            "type": "web_image",
            "url": img.url,
            "base64": b64,
            "title": img.title or "[Image]",
            "alt": img.alt or "",
        })

    # Generated images — may require auth cookies
    for img in chosen.generated_images:
        b64 = await fetch_image_as_base64(img.url, cookies=gemini_cookies)
        result.append({
            "type": "generated_image",
            "url": img.url,
            "base64": b64,
            "title": img.title or "[Generated Image]",
            "alt": img.alt or "",
        })

    return result


# ---------------------------------------------------------------------------
# Cleanup temp files
# ---------------------------------------------------------------------------
def cleanup_temp_files(paths: list[Path]) -> None:
    """Delete a list of temp files, logging any errors."""
    for p in paths:
        try:
            if p and p.exists() and p.is_relative_to(_TEMP_DIR):
                p.unlink()
                logger.debug(f"Cleaned up temp file: {p}")
        except Exception as exc:
            logger.warning(f"Failed to delete temp file {p}: {exc}")
