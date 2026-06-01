# tests/test_gemini_serialization.py
import pytest
import json
from app.services.providers.exceptions import StateIntegrityError
from app.services.providers.gemini.persistence import serialize_session_state, deserialize_session_state

class MockGem:
    def __init__(self, gem_id):
        self.id = gem_id

class MockChatSession:
    def __init__(self, metadata, model, gem):
        self._ChatSession__metadata = metadata
        self.model = model
        self.gem = gem

    @property
    def metadata(self):
        return self._ChatSession__metadata

class MockGeminiClient:
    def start_chat(self, model, gem):
        return MockChatSession(
            metadata=["", "", "", None, None, None, None, None, None, ""],
            model=model,
            gem=gem
        )

def test_serialize_session_state():
    session = MockChatSession(
        metadata=["cid", "rid", "rcid", None, None, None, None, None, None, "context"],
        model="gemini-3-flash",
        gem=MockGem("custom-gem")
    )
    
    state_str = serialize_session_state(session)
    data = json.loads(state_str)
    
    assert data["provider_state_version"] == 1
    assert data["metadata"] == ["cid", "rid", "rcid", None, None, None, None, None, None, "context"]
    assert data["gem_id"] == "custom-gem"
    assert data["model_name"] == "gemini-3-flash"

def test_deserialize_session_state():
    client = MockGeminiClient()
    state_payload = {
        "provider_state_version": 1,
        "metadata": ["c", "r", "rc", None, None, None, None, None, None, "ctx"],
        "gem_id": "my-gem",
        "model_name": "pro"
    }
    state_str = json.dumps(state_payload)
    
    session = deserialize_session_state(state_str, client)
    
    assert session.model == "pro"
    assert session.gem == "my-gem"
    assert session.metadata == ["c", "r", "rc", None, None, None, None, None, None, "ctx"]
    # Verify isolation
    assert id(session.metadata) != id(state_payload["metadata"])

def test_deserialize_session_state_allows_variable_length_metadata():
    client = MockGeminiClient()
    state_payload = {
        "provider_state_version": 1,
        "metadata": ["c", "r", "rc"],
        "gem_id": "my-gem",
        "model_name": "pro"
    }

    session = deserialize_session_state(json.dumps(state_payload), client)

    assert session.metadata == ["c", "r", "rc"]

def test_deserialize_session_state_allows_metadata_longer_than_ten_fields():
    client = MockGeminiClient()
    metadata = ["c", "r", "rc", None, None, None, None, None, None, "ctx", "future-field"]
    state_payload = {
        "provider_state_version": 1,
        "metadata": metadata,
        "gem_id": "my-gem",
        "model_name": "pro"
    }

    session = deserialize_session_state(json.dumps(state_payload), client)

    assert session.metadata == metadata

def test_deserialize_invalid_version():
    client = MockGeminiClient()
    state_payload = {
        "provider_state_version": 2,  # Incompatible version
        "metadata": ["c", "r", "rc", None, None, None, None, None, None, "ctx"],
        "gem_id": "my-gem",
        "model_name": "pro"
    }
    with pytest.raises(StateIntegrityError) as exc_info:
        deserialize_session_state(json.dumps(state_payload), client)
    assert "Unsupported provider state version" in str(exc_info.value)

def test_deserialize_invalid_metadata():
    client = MockGeminiClient()
    
    # 1. Non-list metadata
    state_payload = {
        "provider_state_version": 1,
        "metadata": "invalid",
        "gem_id": "my-gem",
        "model_name": "pro"
    }
    with pytest.raises(StateIntegrityError):
        deserialize_session_state(json.dumps(state_payload), client)
        
    # 2. Empty metadata list cannot restore provider continuity.
    state_payload["metadata"] = []
    with pytest.raises(StateIntegrityError):
        deserialize_session_state(json.dumps(state_payload), client)

    # 3. Fewer than cid/rid/rcid cannot restore provider continuity.
    state_payload["metadata"] = ["c", "r"]
    with pytest.raises(StateIntegrityError):
        deserialize_session_state(json.dumps(state_payload), client)

@pytest.mark.parametrize(
    "metadata",
    [
        [None, "r", "rc"],
        ["", "r", "rc"],
        [123, "r", "rc"],
        ["c", None, "rc"],
        ["c", "", "rc"],
        ["c", 123, "rc"],
        ["c", "r", None],
        ["c", "r", ""],
        ["c", "r", 123],
    ],
)
def test_deserialize_rejects_invalid_continuation_fields(metadata):
    client = MockGeminiClient()
    state_payload = {
        "provider_state_version": 1,
        "metadata": metadata,
        "gem_id": "my-gem",
        "model_name": "pro"
    }

    with pytest.raises(StateIntegrityError):
        deserialize_session_state(json.dumps(state_payload), client)

def test_deserialize_malformed_json_string():
    client = MockGeminiClient()

    with pytest.raises(StateIntegrityError):
        deserialize_session_state("{not-json", client)

def test_deserialize_missing_required_field():
    client = MockGeminiClient()
    state_payload = {
        "provider_state_version": 1,
        "metadata": ["c", "r", "rc"],
        "model_name": "pro"
    }

    with pytest.raises(StateIntegrityError):
        deserialize_session_state(json.dumps(state_payload), client)
