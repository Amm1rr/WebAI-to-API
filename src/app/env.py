import os
from pathlib import Path


def load_local_env() -> None:
    """Load simple KEY=VALUE pairs from local env files without extra deps."""
    project_root = Path(__file__).resolve().parents[2]

    for file_name in (".env.local", ".env"):
        env_path = project_root / file_name
        if not env_path.exists():
            continue

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key:
                os.environ.setdefault(key, value)
