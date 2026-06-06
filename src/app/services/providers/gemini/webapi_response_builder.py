from __future__ import annotations

from typing import Any, Optional

from app.services.providers.gemini.shared import convert_to_openai_format

ARTIFACT_PROVIDER = "gemini_webapi"


def _string_value(value: Any) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    return None


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _build_image_artifact(image: Any) -> Optional[dict]:
    artifact: dict[str, str] = {
        "type": "image",
        "provider": ARTIFACT_PROVIDER,
    }

    title = _string_value(getattr(image, "title", None))
    url = _string_value(getattr(image, "url", None))
    alt = _string_value(getattr(image, "alt", None))

    if title:
        artifact["title"] = title
    if url:
        artifact["url"] = url
    if alt:
        artifact["alt"] = alt

    return artifact if len(artifact) > 2 else None


def _build_video_artifact(video: Any) -> Optional[dict]:
    artifact: dict[str, str] = {
        "type": "video",
        "provider": ARTIFACT_PROVIDER,
    }

    title = _string_value(getattr(video, "title", None))
    url = _string_value(getattr(video, "url", None))
    thumbnail_url = _string_value(getattr(video, "thumbnail", None))

    if title:
        artifact["title"] = title
    if url:
        artifact["url"] = url
    if thumbnail_url:
        artifact["thumbnail_url"] = thumbnail_url

    return artifact if len(artifact) > 2 else None


def _build_audio_artifact(media: Any) -> Optional[dict]:
    artifact: dict[str, str] = {
        "type": "audio",
        "provider": ARTIFACT_PROVIDER,
    }

    title = _string_value(getattr(media, "title", None))
    url = _string_value(getattr(media, "mp3_url", None)) or _string_value(
        getattr(media, "url", None)
    )
    thumbnail_url = _string_value(getattr(media, "mp3_thumbnail", None)) or _string_value(
        getattr(media, "thumbnail", None)
    )

    if title:
        artifact["title"] = title
    if url:
        artifact["url"] = url
    if thumbnail_url:
        artifact["thumbnail_url"] = thumbnail_url

    return artifact if len(artifact) > 2 else None


def build_choice_artifacts(response: Any) -> list[dict]:
    artifacts: list[dict] = []

    for image in _list_value(getattr(response, "images", [])):
        artifact = _build_image_artifact(image)
        if artifact:
            artifacts.append(artifact)

    for video in _list_value(getattr(response, "videos", [])):
        artifact = _build_video_artifact(video)
        if artifact:
            artifacts.append(artifact)

    for media in _list_value(getattr(response, "media", [])):
        artifact = _build_audio_artifact(media)
        if artifact:
            artifacts.append(artifact)

    return artifacts


def build_webapi_chat_completion_response(
    response: Any,
    model: str,
    *,
    tool_call: Optional[dict] = None,
    conversation_id: Optional[str] = None,
    reused_conversation: bool = False,
) -> dict:
    openai_response = convert_to_openai_format(
        getattr(response, "text", "") or "",
        model,
        False,
        tool_call,
    )

    artifacts = build_choice_artifacts(response)
    if artifacts:
        openai_response["choices"][0]["artifacts"] = artifacts

    if conversation_id is not None:
        openai_response["conversation_id"] = conversation_id
        openai_response["reused_conversation"] = reused_conversation

    return openai_response


def build_webapi_streaming_artifact_chunk(
    response: Any,
    model: str,
    *,
    conversation_id: Optional[str] = None,
    reused_conversation: bool = False,
) -> Optional[dict]:
    artifacts = build_choice_artifacts(response)
    if not artifacts:
        return None

    openai_chunk = convert_to_openai_format("", model, True)
    openai_chunk["choices"][0]["delta"] = {}
    openai_chunk["choices"][0]["artifacts"] = artifacts
    openai_chunk["conversation_id"] = conversation_id
    openai_chunk["reused_conversation"] = reused_conversation
    return openai_chunk
