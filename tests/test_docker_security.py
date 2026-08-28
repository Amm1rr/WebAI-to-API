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
    dockerfile = _read("Dockerfile")
    docker_doc = _read("docs/docker.md")
    dashboard_doc = _read("docs/dashboard.md")

    assert "APP_UID: ${APP_UID:-1000}" in compose
    assert "APP_GID: ${APP_GID:-1000}" in compose
    assert '- "${DOCKER_BIND_ADDRESS:-127.0.0.1}:${WEB_PORT:-6969}:6969"' in compose
    assert "# Docker host interface for published port. Default is local-only IPv4 loopback." in env_example
    assert "# DOCKER_BIND_ADDRESS=127.0.0.1" in env_example
    assert "# Set DOCKER_BIND_ADDRESS=0.0.0.0 for explicit LAN/server access." in env_example
    assert "# WEB_PORT=8080" in env_example
    assert "container application port remains 6969" in env_example
    assert 'CMD ["python", "src/run.py", "--host", "0.0.0.0", "--port", "6969"]' in dockerfile
    assert "127.0.0.1" in docker_doc
    assert "DOCKER_BIND_ADDRESS=0.0.0.0" in docker_doc
    assert "entire service" in docker_doc
    assert "no caller API authentication" in docker_doc
    assert "entire unauthenticated service" in dashboard_doc
    assert makefile.count("--build-arg APP_UID=$${APP_UID:-1000}") == 2
    assert makefile.count("--build-arg APP_GID=$${APP_GID:-1000}") == 2
    assert "source: ./config.conf" in compose
    assert "target: /app/config.conf" in compose
    assert "source: ${DOCKER_RUNTIME_DIR:-./runtime}" in compose
    assert "target: /app/runtime" in compose
    assert "- RUNTIME_DIR=/app/runtime" in compose
    assert "- AUTH_STATE_DIR=/app/runtime/auth" in compose
    assert "- CONVERSATION_SNAPSHOT_DB=/app/runtime/conversations/conversation_snapshots.db" in compose
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


def _run_compose_config(
    project,
    compose,
    web_port=None,
    bind_address=None,
    docker_runtime_dir=None,
    override=None,
):
    env = os.environ.copy()
    env.pop("WEB_PORT", None)
    env.pop("DOCKER_BIND_ADDRESS", None)
    env.pop("DOCKER_RUNTIME_DIR", None)
    if web_port is not None:
        env["WEB_PORT"] = str(web_port)
    if bind_address is not None:
        env["DOCKER_BIND_ADDRESS"] = str(bind_address)
    if docker_runtime_dir is not None:
        env["DOCKER_RUNTIME_DIR"] = str(docker_runtime_dir)
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(project),
        "-f",
        str(compose),
    ]
    if override is not None:
        command.extend(["-f", str(override)])
    command.extend(["config", "--format", "json"])
    return subprocess.run(command, env=env, capture_output=True, text=True)


def _resolved_port(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["web_ai"]["ports"][0]


def test_compose_resolves_default_and_custom_host_ports(tmp_path):
    _require_docker_compose()
    project, compose = _compose_fixture(tmp_path)

    default_port = _resolved_port(_run_compose_config(project, compose))
    custom_port = _resolved_port(_run_compose_config(project, compose, 8080))

    assert default_port["host_ip"] == "127.0.0.1"
    assert int(default_port["published"]) == 6969
    assert int(default_port["target"]) == 6969
    assert custom_port["host_ip"] == "127.0.0.1"
    assert int(custom_port["published"]) == 8080
    assert int(custom_port["target"]) == 6969


def test_compose_resolves_explicit_broad_bind(tmp_path):
    _require_docker_compose()
    project, compose = _compose_fixture(tmp_path)

    port = _resolved_port(
        _run_compose_config(project, compose, bind_address="0.0.0.0")
    )

    assert port["host_ip"] == "0.0.0.0"
    assert int(port["published"]) == 6969
    assert int(port["target"]) == 6969


def test_compose_resolves_bind_and_port_overrides(tmp_path):
    _require_docker_compose()
    project, compose = _compose_fixture(tmp_path)

    port = _resolved_port(
        _run_compose_config(
            project,
            compose,
            web_port=8080,
            bind_address="0.0.0.0",
        )
    )

    assert port["host_ip"] == "0.0.0.0"
    assert int(port["published"]) == 8080
    assert int(port["target"]) == 6969


def _resolved_service(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["web_ai"]


def test_compose_resolves_runtime_source_and_fixed_container_paths(tmp_path):
    _require_docker_compose()
    project, compose = _compose_fixture(tmp_path)
    custom_source = project / "state with spaces"
    custom_source.mkdir()
    (project / ".env").write_text(
        "RUNTIME_DIR=/native/runtime\n"
        "AUTH_STATE_DIR=/native/auth\n"
        "CONVERSATION_SNAPSHOT_DB=:memory:\n",
        encoding="utf-8",
    )

    default = _resolved_service(_run_compose_config(project, compose))
    custom = _resolved_service(
        _run_compose_config(project, compose, docker_runtime_dir="state with spaces")
    )

    assert default["volumes"][1]["source"] == str(project / "runtime")
    assert custom["volumes"][1]["source"] == str(custom_source)
    assert custom["volumes"][1]["target"] == "/app/runtime"
    environment = custom["environment"]
    assert environment["RUNTIME_DIR"] == "/app/runtime"
    assert environment["AUTH_STATE_DIR"] == "/app/runtime/auth"
    assert environment["CONVERSATION_SNAPSHOT_DB"] == "/app/runtime/conversations/conversation_snapshots.db"


def test_compose_resolves_absolute_runtime_source(tmp_path):
    _require_docker_compose()
    project, compose = _compose_fixture(tmp_path)
    source = tmp_path / "external runtime"
    source.mkdir()

    service = _resolved_service(
        _run_compose_config(project, compose, docker_runtime_dir=source)
    )

    assert service["volumes"][1]["source"] == str(source)
    assert service["volumes"][1]["target"] == "/app/runtime"


def test_compose_override_inherits_secure_port_mapping(tmp_path):
    _require_docker_compose()
    project, compose = _compose_fixture(tmp_path)
    override = project / "docker-compose.override.yml"
    shutil.copy2(ROOT / "docker-compose.override.yml", override)

    port = _resolved_port(_run_compose_config(project, compose, override=override))

    assert port["host_ip"] == "127.0.0.1"
    assert int(port["published"]) == 6969
    assert int(port["target"]) == 6969


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

    assert makefile.count('test -d "$(DOCKER_RUNTIME_DIR)"') == 2
    assert "Docker runtime source" in makefile


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
    assert "Docker runtime source './runtime' missing or is not a directory" in output


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
def test_make_up_uses_custom_runtime_source_with_spaces(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy2(ROOT / "Makefile", project / "Makefile")
    (project / "config.conf").write_text("[General]\n", encoding="utf-8")
    (project / ".env").write_text(
        "DOCKER_RUNTIME_DIR=state with spaces\n", encoding="utf-8"
    )
    source = project / "state with spaces"
    source.mkdir()

    tools = tmp_path / "tools"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ.get('PATH', '')}"}

    result = subprocess.run(
        ["make", "up"], cwd=project, env=env, capture_output=True, text=True
    )

    assert result.returncode == 0


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
