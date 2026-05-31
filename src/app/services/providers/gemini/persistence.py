import json
from typing import Any, Optional
from app.services.providers.exceptions import StateIntegrityError

def serialize_session_state(session: Any) -> str:
    """
    Serializes a Gemini ChatSession's private state into a JSON string.
    """
    if session is None:
        raise ValueError("Session cannot be None.")

    gem_id = None
    if session.gem:
        gem_id = session.gem.id if hasattr(session.gem, 'id') else session.gem

    model_name = ""
    if session.model:
        if isinstance(session.model, str):
            model_name = session.model
        elif isinstance(session.model, dict):
            model_name = session.model.get("model_name", "")
        elif hasattr(session.model, "model_name"):
            model_name = session.model.model_name
        else:
            model_name = str(session.model)

    payload = {
        "provider_state_version": 1,
        "metadata": session.metadata,
        "gem_id": gem_id,
        "model_name": model_name
    }
    return json.dumps(payload)


def validate_session_state_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise StateIntegrityError("Session state must be a JSON object.")

    required_keys = {"provider_state_version", "metadata", "gem_id", "model_name"}
    missing_keys = required_keys - payload.keys()
    if missing_keys:
        raise StateIntegrityError(f"Missing required session state fields: {', '.join(sorted(missing_keys))}")

    version = payload.get("provider_state_version")
    if version != 1:
        raise StateIntegrityError(f"Unsupported provider state version: {version}")

    metadata = payload.get("metadata")
    if not isinstance(metadata, list) or len(metadata) < 3:
        raise StateIntegrityError("Missing or invalid metadata context in session state.")
    if not all(isinstance(value, str) and value for value in metadata[:3]):
        raise StateIntegrityError("Missing or invalid Gemini continuation metadata fields.")


def deserialize_session_state(
    state_str: str,
    client: Any,
    *,
    model: Optional[Any] = None,
    gem: Optional[Any] = None,
) -> Any:
    """
    Deserializes a Gemini ChatSession's state from a JSON string,
    recreates the session using the client, and safely isolates the metadata reference.
    """
    try:
        payload = json.loads(state_str)
    except Exception as e:
        raise StateIntegrityError(f"Malformed JSON state: {e}")

    validate_session_state_payload(payload)

    metadata = payload.get("metadata")
    model_name = model if model is not None else payload.get("model_name")
    gem_id = gem if gem is not None else payload.get("gem_id")

    # Start a clean chat session
    session = client.start_chat(model=model_name, gem=gem_id)

    # Guarantee isolated metadata copy to break shared reference to DEFAULT_METADATA
    session._ChatSession__metadata = list(metadata)

    return session
