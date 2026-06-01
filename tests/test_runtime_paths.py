from app.config import (
    get_default_auth_state_dir,
    get_default_conversation_snapshot_db,
    get_default_playwright_cache_dir,
    load_config,
)


def test_runtime_dir_drives_default_runtime_paths(monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", "custom_runtime")

    assert get_default_auth_state_dir() == "custom_runtime/auth"
    assert (
        get_default_conversation_snapshot_db()
        == "custom_runtime/conversations/conversation_snapshots.db"
    )
    assert get_default_playwright_cache_dir() == "custom_runtime/cache/playwright"


def test_load_config_defaults_auth_state_dir_to_runtime_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", "custom_runtime")
    monkeypatch.delenv("AUTH_STATE_DIR", raising=False)

    config = load_config(str(tmp_path / "config.conf"))

    assert config["Playwright"]["auth_state_dir"] == "custom_runtime/auth"


def test_auth_state_dir_env_override_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", "custom_runtime")
    monkeypatch.setenv("AUTH_STATE_DIR", "legacy_auth_state")

    config = load_config(str(tmp_path / "config.conf"))

    assert config["Playwright"]["auth_state_dir"] == "legacy_auth_state"
