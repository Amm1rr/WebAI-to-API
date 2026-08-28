import os

from app.config import (
    get_default_auth_state_dir,
    get_default_conversation_snapshot_db,
    get_default_playwright_cache_dir,
    get_runtime_dir,
    load_config,
    resolve_auth_state_dir,
    resolve_conversation_snapshot_db,
)
from app import env as env_module


def test_runtime_dir_drives_default_runtime_paths(monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", "custom_runtime")

    assert get_default_auth_state_dir() == "custom_runtime/auth"
    assert (
        get_default_conversation_snapshot_db()
        == "custom_runtime/conversations/conversation_snapshots.db"
    )
    assert get_default_playwright_cache_dir() == "custom_runtime/cache/playwright"


def test_runtime_paths_default_to_relative_runtime(monkeypatch):
    monkeypatch.delenv("RUNTIME_DIR", raising=False)
    monkeypatch.delenv("AUTH_STATE_DIR", raising=False)
    monkeypatch.delenv("CONVERSATION_SNAPSHOT_DB", raising=False)

    assert get_runtime_dir() == "runtime"
    assert get_default_auth_state_dir() == "runtime/auth"
    assert get_default_conversation_snapshot_db() == "runtime/conversations/conversation_snapshots.db"
    assert resolve_auth_state_dir() == "runtime/auth"
    assert resolve_conversation_snapshot_db() == "runtime/conversations/conversation_snapshots.db"


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


def test_explicit_config_auth_path_overrides_runtime_default(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", "custom_runtime")
    monkeypatch.delenv("AUTH_STATE_DIR", raising=False)
    config_path = tmp_path / "config.conf"
    config_path.write_text("[Playwright]\nauth_state_dir = separate_auth\n", encoding="utf-8")

    config = load_config(str(config_path))

    assert config["Playwright"]["auth_state_dir"] == "separate_auth"
    assert resolve_auth_state_dir("separate_auth") == "separate_auth"


def test_auth_state_dir_environment_overrides_explicit_config_path(monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", "custom_runtime")
    monkeypatch.setenv("AUTH_STATE_DIR", "environment_auth")

    assert resolve_auth_state_dir("separate_auth") == "environment_auth"


def test_documented_docker_login_auth_path_overrides_native_configuration(
    tmp_path, monkeypatch
):
    docker_source = tmp_path / "docker runtime"
    expected_auth_dir = docker_source / "auth"
    config_path = tmp_path / "config.conf"
    config_path.write_text(
        f"[Playwright]\nauth_state_dir = {tmp_path / 'conflicting auth'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_RUNTIME_DIR", str(docker_source))
    monkeypatch.setenv("RUNTIME_DIR", str(docker_source))
    monkeypatch.setenv("AUTH_STATE_DIR", "/wrong/environment/auth")

    config = load_config(str(config_path))

    assert resolve_auth_state_dir(config["Playwright"]["auth_state_dir"]) == "/wrong/environment/auth"

    # This is the explicit override supplied by Docker's documented host-login command.
    monkeypatch.setenv("AUTH_STATE_DIR", str(expected_auth_dir))

    assert resolve_auth_state_dir(config["Playwright"]["auth_state_dir"]) == str(
        expected_auth_dir
    )


def test_conversation_database_environment_override_and_memory(monkeypatch):
    monkeypatch.setenv("CONVERSATION_SNAPSHOT_DB", "custom state/conversations.db")
    assert resolve_conversation_snapshot_db() == "custom state/conversations.db"

    monkeypatch.setenv("CONVERSATION_SNAPSHOT_DB", ":memory:")
    assert resolve_conversation_snapshot_db() == ":memory:"


def test_local_env_precedence_remains_process_then_local_then_env(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    app_file = project_root / "src" / "app" / "env.py"
    app_file.parent.mkdir(parents=True)
    (project_root / ".env").write_text("LOCAL_ENV_TEST_KEY=env-value\n", encoding="utf-8")
    (project_root / ".env.local").write_text("LOCAL_ENV_TEST_KEY=local-value\n", encoding="utf-8")
    monkeypatch.setattr(env_module, "__file__", str(app_file))
    monkeypatch.delenv("LOCAL_ENV_TEST_KEY", raising=False)

    env_module.load_local_env()
    assert os.environ["LOCAL_ENV_TEST_KEY"] == "local-value"

    monkeypatch.setenv("LOCAL_ENV_TEST_KEY", "process-value")
    env_module.load_local_env()
    assert os.environ["LOCAL_ENV_TEST_KEY"] == "process-value"
