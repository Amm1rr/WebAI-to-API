# src/app/endpoints/files.py
"""
File upload endpoint — allows clients to upload images or PDFs and receive
a file_id that can be referenced in subsequent /gemini or /v1/chat/completions
requests via the `files` field.

Compatible with the OpenAI Files API surface (subset).
"""

import hashlib
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.logger import logger
from app.utils.image_utils import ALLOWED_MIME_TYPES, get_temp_dir

router = APIRouter()

_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "application/pdf": ".pdf",
}


@router.post("/v1/files")
async def upload_file(file: UploadFile = File(...)):
    """
    Upload an image or PDF file.

    Returns a ``file_id`` (and ``local_path``) that you can pass to:
    - ``POST /gemini`` → ``files: ["<local_path>"]``
    - ``POST /v1/chat/completions`` → content part ``{"type": "image_url", "image_url": {"url": "file://<file_id>"}}``
    """
    content = await file.read()

    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")

    # Determine content type — prefer header, fall back to filename extension
    content_type = (file.content_type or "").split(";")[0].strip()
    if content_type and content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {content_type}. Allowed: {sorted(ALLOWED_MIME_TYPES)}",
        )

    # Derive file extension
    if content_type and content_type in _MIME_TO_EXT:
        ext = _MIME_TO_EXT[content_type]
    else:
        ext = Path(file.filename or "").suffix or ".bin"

    # Unique, deterministic-ish file ID
    file_hash = hashlib.md5(content).hexdigest()[:8]
    timestamp = int(time.time() * 1000)
    file_id = f"file_{timestamp}_{file_hash}{ext}"

    dest = get_temp_dir() / file_id
    dest.write_bytes(content)
    logger.info(f"File uploaded: {file_id} ({len(content)} bytes, type={content_type})")

    return {
        "id": file_id,
        "object": "file",
        "bytes": len(content),
        "filename": file.filename,
        "content_type": content_type,
        "purpose": "vision",
        "local_path": str(dest),
    }


@router.get("/v1/files/{file_id}")
async def get_file_info(file_id: str):
    """Return metadata for a previously uploaded file."""
    # Sanitize: prevent path traversal
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(status_code=400, detail="Invalid file_id")

    dest = get_temp_dir() / file_id
    if not dest.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")

    return {
        "id": file_id,
        "object": "file",
        "bytes": dest.stat().st_size,
        "local_path": str(dest),
    }


@router.delete("/v1/files/{file_id}")
async def delete_file(file_id: str):
    """Delete a previously uploaded file."""
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(status_code=400, detail="Invalid file_id")

    dest = get_temp_dir() / file_id
    if not dest.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")

    dest.unlink()
    logger.info(f"File deleted: {file_id}")
    return {"id": file_id, "object": "file", "deleted": True}
