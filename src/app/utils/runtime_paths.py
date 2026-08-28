import os


def get_runtime_dir() -> str:
    return os.environ.get("RUNTIME_DIR", "runtime")


def get_default_auth_state_dir() -> str:
    return os.path.join(get_runtime_dir(), "auth")


def get_default_conversation_snapshot_db() -> str:
    return os.path.join(get_runtime_dir(), "conversations", "conversation_snapshots.db")


def get_default_playwright_cache_dir() -> str:
    return os.path.join(get_runtime_dir(), "cache", "playwright")


def resolve_auth_state_dir(configured_auth_state_dir: str | None = None) -> str:
    return os.environ.get("AUTH_STATE_DIR") or configured_auth_state_dir or get_default_auth_state_dir()


def resolve_conversation_snapshot_db() -> str:
    return os.environ.get("CONVERSATION_SNAPSHOT_DB") or get_default_conversation_snapshot_db()
