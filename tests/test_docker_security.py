import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_dockerfile_uses_configurable_non_root_playwright_user():
    dockerfile = _read("Dockerfile")

    assert "ARG APP_UID=1000" in dockerfile
    assert "ARG APP_GID=1000" in dockerfile
    assert "USER pwuser" in dockerfile
    assert "USER root" not in dockerfile


def test_dockerfile_handles_only_known_ubuntu_uid_gid_conflicts():
    dockerfile = _read("Dockerfile")
    remapping = dockerfile.split("# Keep image ownership configurable", 1)[1]

    assert re.search(r'getent passwd "\$APP_UID"', remapping)
    assert re.search(r'getent group "\$APP_GID"', remapping)
    assert '[ "$uid_owners" = "ubuntu" ]' in remapping
    assert '[ "$gid_owners" = "ubuntu" ]' in remapping
    assert "groupmod --gid \"$temp_gid\" ubuntu" in remapping
    assert "usermod --uid \"$temp_uid\" ubuntu" in remapping
    assert "usermod --gid \"$temp_gid\" ubuntu" in remapping
    assert "unrelated user(s)" in remapping
    assert "unrelated group(s)" in remapping
    assert "primary group for unrelated user(s)" in remapping
    assert 'id -u pwuser' in remapping
    assert 'id -g pwuser' in remapping
    assert 'chown -R "$APP_UID:$APP_GID" /home/pwuser' in remapping
    assert "/app/runtime" not in remapping
    assert "chmod 777" not in remapping
    assert "userdel" not in remapping
    assert "groupdel" not in remapping


def test_compose_passes_uid_gid_build_contract_and_preserves_mounts():
    compose = _read("docker-compose.yml")
    env_example = _read(".env.example")
    makefile = _read("Makefile")

    assert "APP_UID: ${APP_UID:-1000}" in compose
    assert "APP_GID: ${APP_GID:-1000}" in compose
    assert '- "${WEB_PORT:-6969}:6969"' in compose
    assert "# Optional Docker host port. Container application port remains 6969." in env_example
    assert "# WEB_PORT=8080" in env_example
    assert makefile.count("--build-arg APP_UID=$${APP_UID:-1000}") == 2
    assert makefile.count("--build-arg APP_GID=$${APP_GID:-1000}") == 2
    assert "source: ./config.conf" in compose
    assert "target: /app/config.conf" in compose
    assert "source: ./runtime" in compose
    assert "target: /app/runtime" in compose
    assert compose.count("create_host_path: false") == 2
    assert "read_only: true" in compose


def _require_docker_compose():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable")
    result = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Docker Compose plugin unavailable")


def _compose_fixture(tmp_path):
    project = tmp_path / "compose-project"
    project.mkdir()
    compose = project / "docker-compose.yml"
    shutil.copy2(ROOT / "docker-compose.yml", compose)
    (project / ".env").write_text("", encoding="utf-8")
    (project / "config.conf").write_text("[General]\n", encoding="utf-8")
    (project / "runtime").mkdir()
    return project, compose


def _run_compose_config(project, compose, web_port=None):
    env = os.environ.copy()
    env.pop("WEB_PORT", None)
    if web_port is not None:
        env["WEB_PORT"] = str(web_port)
    return subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(project),
            "-f",
            str(compose),
            "config",
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
    )


def _resolved_port(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["web_ai"]["ports"][0]


def test_compose_resolves_default_and_custom_host_ports(tmp_path):
    _require_docker_compose()
    project, compose = _compose_fixture(tmp_path)

    default_port = _resolved_port(_run_compose_config(project, compose))
    custom_port = _resolved_port(_run_compose_config(project, compose, 8080))

    assert int(default_port["published"]) == 6969
    assert int(default_port["target"]) == 6969
    assert int(custom_port["published"]) == 8080
    assert int(custom_port["target"]) == 6969


def test_compose_rejects_invalid_host_port(tmp_path):
    _require_docker_compose()
    project, compose = _compose_fixture(tmp_path)

    result = _run_compose_config(project, compose, "not-a-port")

    assert result.returncode != 0


def test_dockerignore_excludes_local_credentials_and_state():
    patterns = set(_read(".dockerignore").splitlines())

    assert ".env.*" in patterns
    assert "!.env.example" in patterns
    assert "config.conf.*" in patterns
    assert "!config.conf.example" in patterns
    for pattern in (
        "runtime/",
        "auth_state/",
        "har_and_cookies/",
        ".playwright_data/",
        "generated_media/",
        "*.db",
        "*.db-*",
        "*.sqlite",
        "*.sqlite3",
    ):
        assert pattern in patterns


def test_make_docker_start_requires_host_runtime_directory():
    makefile = _read("Makefile")

    assert makefile.count("test -d runtime") == 2
    assert "runtime missing or is not a directory" in makefile


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("make") is None,
    reason="POSIX make workflow",
)
def test_make_up_rejects_missing_runtime_before_docker(tmp_path):
    project = tmp_path / "project with spaces"
    project.mkdir()
    shutil.copy2(ROOT / "Makefile", project / "Makefile")
    (project / "config.conf").write_text("[General]\n", encoding="utf-8")
    (project / ".env").write_text("", encoding="utf-8")

    result = subprocess.run(
        ["make", "up"],
        cwd=project,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "runtime missing or is not a directory" in output


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("make") is None,
    reason="POSIX make workflow",
)
def test_make_up_preserves_existing_runtime_before_docker(tmp_path):
    project = tmp_path / "project with spaces"
    project.mkdir()
    shutil.copy2(ROOT / "Makefile", project / "Makefile")
    (project / "config.conf").write_text("[General]\n", encoding="utf-8")
    (project / ".env").write_text("", encoding="utf-8")
    runtime = project / "runtime"
    runtime.mkdir()
    marker = runtime / "host-owned-state"
    marker.write_text("keep", encoding="utf-8")

    tools = tmp_path / "tools"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tools}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        ["make", "up"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("make") is None,
    reason="POSIX make workflow",
)
def test_make_build_forwards_uid_gid_to_docker(tmp_path):
    project = tmp_path / "project with spaces"
    project.mkdir()
    shutil.copy2(ROOT / "Makefile", project / "Makefile")
    log = tmp_path / "docker-args"

    tools = tmp_path / "tools"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$DOCKER_ARGS_LOG\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tools}{os.pathsep}{env.get('PATH', '')}",
            "APP_UID": "1234",
            "APP_GID": "2345",
            "DOCKER_ARGS_LOG": str(log),
        }
    )

    result = subprocess.run(
        ["make", "build"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    args = log.read_text(encoding="utf-8")
    assert "--build-arg APP_UID=1234" in args
    assert "--build-arg APP_GID=2345" in args
