import re
import os
import pytest

def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

def test_playwright_version_alignment():
    """
    Ensures that the Playwright version in poetry.lock matches
    both requirements.txt and Dockerfile.
    """
    root = get_project_root()
    
    poetry_lock_path = os.path.join(root, "poetry.lock")
    requirements_path = os.path.join(root, "requirements.txt")
    dockerfile_path = os.path.join(root, "Dockerfile")
    
    poetry_lock_content = read_file(poetry_lock_path)
    requirements_content = read_file(requirements_path)
    dockerfile_content = read_file(dockerfile_path)
    
    # Extract version from poetry.lock
    # Looking for:
    # [[package]]
    # name = "playwright"
    # version = "1.60.0"
    lock_match = re.search(r'\[\[package\]\]\nname = "playwright"\nversion = "([^"]+)"', poetry_lock_content)
    assert lock_match is not None, "Could not find Playwright version in poetry.lock"
    lock_version = lock_match.group(1)
    
    # Extract version from requirements.txt
    # Looking for:
    # playwright==1.60.0 ; ... or playwright==1.60.0\n
    req_match = re.search(r'^playwright==([^\s;]+)', requirements_content, re.MULTILINE)
    assert req_match is not None, "Could not find Playwright version in requirements.txt"
    requirements_version = req_match.group(1)
    
    # Extract version from Dockerfile
    # Looking for:
    # FROM mcr.microsoft.com/playwright/python:v1.60.0-noble
    docker_match = re.search(r'^FROM mcr\.microsoft\.com/playwright/python:v([^\-]+)-', dockerfile_content, re.MULTILINE)
    assert docker_match is not None, "Could not find Playwright base image version in Dockerfile"
    docker_version = docker_match.group(1)
    
    assert lock_version == requirements_version == docker_version, (
        f"Playwright version mismatch!\n"
        f"poetry.lock:      {lock_version}\n"
        f"requirements.txt: {requirements_version}\n"
        f"Dockerfile:       {docker_version}\n"
        f"Run 'make export-reqs' and update Dockerfile to match poetry.lock."
    )


def test_env_not_tracked():
    """
    Repository hygiene: .env holds user-managed secrets/configuration and must
    never return to Git tracking; the updater's `git reset --hard` would
    otherwise overwrite local environment configuration on every update.
    `.env.example` must remain tracked as the documented template.
    """
    import subprocess

    root = get_project_root()
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    assert ".env" not in tracked, (
        ".env is tracked again. User-local secrets/configuration must stay "
        "untracked: run `git rm --cached --sparse .env` and commit."
    )
    assert ".env.example" in tracked, ".env.example template disappeared from tracking"


def test_python_version_contract_alignment():
    """
    Ensures BOTH scripts/bootstrap.py and scripts/doctor.py mirror the
    authoritative Python range declared in pyproject.toml (requires-python).
    """
    import ast

    root = get_project_root()
    pyproject_content = read_file(os.path.join(root, "pyproject.toml"))

    requires_match = re.search(r'requires-python\s*=\s*"([^"]+)"', pyproject_content)
    assert requires_match is not None, "Could not find requires-python in pyproject.toml"
    match = re.fullmatch(r">=(\d+)\.(\d+),<(\d+)\.(\d+)", requires_match.group(1))
    assert match is not None, f"Unsupported requires-python shape: {requires_match.group(1)}"
    expected_min = (int(match.group(1)), int(match.group(2)))
    expected_max = (int(match.group(3)), int(match.group(4)))

    for script in ("bootstrap.py", "doctor.py"):
        script_path = os.path.join(root, "scripts", script)
        tree = ast.parse(read_file(script_path))
        constants = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in (
                        "REQUIRED_PYTHON_VERSION",
                        "MAX_PYTHON_VERSION",
                    ):
                        constants[target.id] = ast.literal_eval(node.value)

        assert constants.get("REQUIRED_PYTHON_VERSION") == expected_min, (
            f"{script} minimum Python {constants.get('REQUIRED_PYTHON_VERSION')} "
            f"does not match pyproject.toml floor {expected_min}."
        )
        assert constants.get("MAX_PYTHON_VERSION") == expected_max, (
            f"{script} maximum Python {constants.get('MAX_PYTHON_VERSION')} "
            f"does not match pyproject.toml ceiling {expected_max}."
        )
