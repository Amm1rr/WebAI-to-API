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
