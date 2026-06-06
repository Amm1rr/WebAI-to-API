import asyncio
import base64
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, Iterable, Optional

from fastapi import HTTPException

MAX_FILE_COUNT = 8
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_FILENAME_LENGTH = 255
MAX_TEXT_CONTROL_CHAR_RATIO = 0.01

ALLOWED_FILE_MIME_TYPES: dict[str, set[str]] = {
    "application/pdf": {".pdf"},
    "application/msword": {".doc"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "application/json": {".json"},
    "application/xml": {".xml"},
    "text/xml": {".xml"},
    "text/plain": {".txt", ".text", ".md", ".csv", ".log"},
    "text/markdown": {".md", ".markdown"},
    "text/csv": {".csv"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/webp": {".webp"},
    "image/gif": {".gif"},
}

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]*);base64,(?P<data>.+)$", re.IGNORECASE | re.DOTALL)


@dataclass
class NormalizedOpenAIChatMessages:
    messages: list[dict[str, Any]]
    files: list[Path] = field(default_factory=list)
    cleanup_dir: Optional[Path] = None
    _tempdir: Optional[tempfile.TemporaryDirectory[str]] = field(default=None, repr=False)


def _raise_validation_error(detail: str, *, status_code: int = 400) -> None:
    raise HTTPException(status_code=status_code, detail=detail)


def _raise_payload_too_large(detail: str) -> None:
    _raise_validation_error(detail, status_code=413)


def _message_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    _raise_validation_error("Invalid message object.")
    return {}


def _part_to_dict(part: Any) -> dict[str, Any]:
    if isinstance(part, dict):
        return dict(part)
    if hasattr(part, "model_dump"):
        return part.model_dump(exclude_none=True)
    _raise_validation_error("Invalid content part.")
    return {}


def _sanitize_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.strip():
        _raise_validation_error("File filename is required.")
    if len(filename) > MAX_FILENAME_LENGTH:
        _raise_validation_error("File filename is too long.")
    if filename in {".", ".."}:
        _raise_validation_error("File filename is invalid.")
    if filename != PurePath(filename).name:
        _raise_validation_error("File filename must not contain path separators.")
    if os.path.isabs(filename):
        _raise_validation_error("File filename must not be an absolute path.")
    if "\x00" in filename:
        _raise_validation_error("File filename contains invalid characters.")
    return filename


def _parse_data_url(file_data: str) -> tuple[str, bytes]:
    if not isinstance(file_data, str) or not file_data:
        _raise_validation_error("File data is required.")
    if file_data.startswith("http://") or file_data.startswith("https://"):
        _raise_validation_error("Remote file URLs are not supported.")
    if os.path.isabs(file_data) or file_data.startswith(("../", "..\\", "/", "\\")):
        _raise_validation_error("Filesystem paths are not supported for file uploads.")

    match = _DATA_URL_RE.match(file_data)
    if not match:
        _raise_validation_error("File data must be a base64 data URL.")

    mime_type = match.group("mime").strip().lower()
    payload = match.group("data").strip()
    if mime_type and mime_type not in ALLOWED_FILE_MIME_TYPES:
        _raise_validation_error(f"Unsupported file MIME type: {mime_type}")

    try:
        decoded = base64.b64decode(payload, validate=True)
    except Exception as exc:
        _raise_validation_error("File data is not valid base64.")
        raise exc

    return mime_type, decoded


def _is_text_like_bytes(decoded: bytes) -> bool:
    if not decoded:
        return True

    try:
        text = decoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False

    if "\x00" in text:
        return False

    control_chars = 0
    for char in text:
        code_point = ord(char)
        if code_point in {9, 10, 11, 12, 13}:
            continue
        if code_point < 32 or code_point == 127:
            control_chars += 1

    return (control_chars / len(text)) <= MAX_TEXT_CONTROL_CHAR_RATIO


def _validate_extensionless_text(filename: str, mime_type: str, decoded: bytes) -> str:
    if mime_type not in {"", "text/plain"}:
        _raise_validation_error("Extensionless files must use an empty MIME type or text/plain.")
    if not _is_text_like_bytes(decoded):
        _raise_validation_error(f"Extensionless file '{filename}' must contain UTF-8 plain text.")
    return "text/plain"


def _validate_extension(filename: str, mime_type: str) -> None:
    suffix = Path(filename).suffix.lower()
    if not suffix:
        _raise_validation_error("File filename must include an extension.")
    allowed_suffixes = ALLOWED_FILE_MIME_TYPES.get(mime_type, set())
    if suffix not in allowed_suffixes:
        _raise_validation_error(
            f"File extension '{suffix}' is not allowed for MIME type '{mime_type}'."
        )


def _join_text_parts(parts: Iterable[dict[str, Any]]) -> str:
    texts: list[str] = []
    for part in parts:
        if part.get("type") == "text":
            text = part.get("text")
            if text is None:
                _raise_validation_error("Text content parts must include text.")
            texts.append(str(text))
    return "\n\n".join(texts).strip()


def normalize_openai_chat_messages(
    messages: list[Any],
    *,
    allow_file_parts: bool,
) -> NormalizedOpenAIChatMessages:
    normalized_messages: list[dict[str, Any]] = []
    staged_files: list[Path] = []
    total_file_bytes = 0
    file_count = 0
    tempdir: Optional[tempfile.TemporaryDirectory[str]] = None
    cleanup_dir: Optional[Path] = None

    try:
        for raw_message in messages:
            message = _message_to_dict(raw_message)
            content = message.get("content")

            if isinstance(content, str) or content is None:
                normalized_messages.append(message)
                continue

            if not isinstance(content, list):
                _raise_validation_error("Message content must be a string or an array of content parts.")

            role = message.get("role")
            text_parts: list[dict[str, Any]] = []
            file_parts_present = False

            for raw_part in content:
                part = _part_to_dict(raw_part)
                part_type = part.get("type")

                if part_type == "text":
                    text_parts.append(part)
                    continue

                if part_type != "file":
                    _raise_validation_error(f"Unsupported content part type: {part_type}")

                file_parts_present = True
                if not allow_file_parts:
                    _raise_validation_error(
                        "File content parts are only supported on the Gemini WebAPI backend."
                    )
                if role != "user":
                    _raise_validation_error("File content parts are only supported in user messages.")

                file_payload = part.get("file") or {}
                if not isinstance(file_payload, dict):
                    _raise_validation_error("File content parts must include a file object.")

                filename = _sanitize_filename(file_payload.get("filename", ""))
                mime_type, decoded = _parse_data_url(file_payload.get("file_data", ""))
                if Path(filename).suffix.lower():
                    _validate_extension(filename, mime_type)
                else:
                    mime_type = _validate_extensionless_text(filename, mime_type, decoded)

                decoded_size = len(decoded)
                if decoded_size > MAX_FILE_SIZE_BYTES:
                    _raise_payload_too_large(
                        f"File '{filename}' exceeds the maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes."
                    )
                if file_count + 1 > MAX_FILE_COUNT:
                    _raise_payload_too_large(
                        f"Too many files in one request. Maximum allowed is {MAX_FILE_COUNT}."
                    )
                if total_file_bytes + decoded_size > MAX_TOTAL_FILE_SIZE_BYTES:
                    _raise_payload_too_large(
                        f"Total uploaded file size exceeds the maximum allowed size of {MAX_TOTAL_FILE_SIZE_BYTES} bytes."
                    )

                if tempdir is None:
                    tempdir = tempfile.TemporaryDirectory(prefix="webai_uploads_")
                    cleanup_dir = Path(tempdir.name)

                assert cleanup_dir is not None
                staged_name = f"{file_count:02d}_{filename}"
                staged_path = cleanup_dir / staged_name
                staged_path.write_bytes(decoded)
                staged_files.append(staged_path)
                file_count += 1
                total_file_bytes += decoded_size

            if file_parts_present or text_parts or content == []:
                if text_parts:
                    message["content"] = _join_text_parts(text_parts)
                else:
                    message["content"] = ""

            normalized_messages.append(message)
    except Exception:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
        raise

    return NormalizedOpenAIChatMessages(
        messages=normalized_messages,
        files=staged_files,
        cleanup_dir=cleanup_dir,
        _tempdir=tempdir,
    )


async def cleanup_staged_files(normalized: NormalizedOpenAIChatMessages) -> None:
    async def _cleanup() -> None:
        cleanup_dir = normalized.cleanup_dir
        if cleanup_dir is None:
            return
        shutil.rmtree(cleanup_dir, ignore_errors=True)
        normalized.cleanup_dir = None
        normalized._tempdir = None

    await asyncio.shield(_cleanup())
