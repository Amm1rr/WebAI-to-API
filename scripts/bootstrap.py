import os
import sys
import shutil
import subprocess
import argparse
import configparser
from pathlib import Path

# Import platform utils
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from app.utils.python_version import (
    SUPPORTED_RANGE_TEXT,
    WINDOWS_SUPPORTED_RANGE_TEXT,
    is_supported_python,
)
from app.env import load_local_env
from app.utils.runtime_paths import (
    get_default_conversation_snapshot_db,
    get_runtime_dir,
    resolve_auth_state_dir,
    resolve_conversation_snapshot_db,
)

try:
    from platform_utils import get_linux_distro
except ImportError:
    def get_linux_distro():
        return None, "Unknown", False

CONFIG_FILE = "config.conf"
CONFIG_EXAMPLE = "config.conf.example"
ENV_FILE = ".env"
ENV_EXAMPLE = ".env.example"
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700

def print_step(message):
    print(f"--> {message}")

def print_error(message):
    print(f"ERROR: {message}", file=sys.stderr)

def _harden_posix_mode(path, mode):
    if os.name != "posix":
        return True
    try:
        os.chmod(path, mode)
    except OSError as error:
        print_error(f"Cannot secure {path}: {error}")
        return False
    return True

def check_python_version():
    current_version = sys.version_info
    platform_name = "nt" if sys.platform == "win32" else "posix"
    if is_supported_python(current_version, platform_name=platform_name):
        return True

    if platform_name == "nt":
        print_error(
            f"Windows Python {WINDOWS_SUPPORTED_RANGE_TEXT} is required for "
            "the secure Gemini WebAPI private cookie cache."
        )
    else:
        print_error(f"Python {SUPPORTED_RANGE_TEXT} is required.")
    print_error(
        f"Current version is {current_version[0]}.{current_version[1]}.{current_version[2]}."
    )
    return False

def check_poetry():
    poetry_path = shutil.which("poetry")
    if not poetry_path:
        print_error("Poetry not found in PATH.")
        print_error("Please install Poetry following the official instructions: https://python-poetry.org/docs/#installation")
        return False
    return True

def get_configured_auth_state_dir():
    config = configparser.ConfigParser()
    try:
        config.read(CONFIG_FILE, encoding="utf-8")
    except (configparser.Error, OSError):
        return None
    return config.get("Playwright", "auth_state_dir", fallback=None)


def get_directory_targets(configured_auth_state_dir=None):
    runtime_dir = get_runtime_dir()
    auth_state_dir = resolve_auth_state_dir(configured_auth_state_dir)
    conversation_db = resolve_conversation_snapshot_db()
    targets = {
        runtime_dir: True,
        os.path.join(runtime_dir, "cache"): True,
        auth_state_dir: True,
    }

    if conversation_db != ":memory:":
        conversation_parent = os.path.dirname(conversation_db) or "."
        default_parent = os.path.dirname(get_default_conversation_snapshot_db())
        targets[conversation_parent] = targets.get(conversation_parent, False) or (
            conversation_parent == default_parent
        )

    return targets


def setup_directories(check_mode=False, configured_auth_state_dir=None):
    for dir_path, harden in get_directory_targets(configured_auth_state_dir).items():
        if not os.path.exists(dir_path):
            if check_mode:
                print_step(f"[DRY-RUN] Would create directory: {dir_path}")
            else:
                try:
                    if os.name == "posix":
                        os.makedirs(dir_path, mode=PRIVATE_DIR_MODE, exist_ok=True)
                    else:
                        os.makedirs(dir_path, exist_ok=True)
                except OSError as error:
                    print_error(f"Cannot create directory {dir_path}: {error}")
                    return False
                print_step(f"Created directory: {dir_path}")
        else:
            print_step(f"Directory already exists: {dir_path}")
        if harden and not check_mode and not _harden_posix_mode(dir_path, PRIVATE_DIR_MODE):
            return False
    return True


def setup_docker_runtime_source(check_mode=False):
    source = os.environ.get("DOCKER_RUNTIME_DIR", "runtime")
    if os.path.exists(source):
        if not os.path.isdir(source):
            print_error(f"Docker runtime source {source} exists but is not a directory.")
            return False
        print_step(f"Docker runtime source already exists: {source}")
        return True

    if check_mode:
        print_step(f"[DRY-RUN] Would create Docker runtime source: {source}")
        return True

    try:
        if os.name == "posix":
            os.makedirs(source, mode=PRIVATE_DIR_MODE, exist_ok=True)
        else:
            os.makedirs(source, exist_ok=True)
    except OSError as error:
        print_error(f"Cannot create Docker runtime source {source}: {error}")
        return False

    print_step(f"Created Docker runtime source: {source}")
    return _harden_posix_mode(source, PRIVATE_DIR_MODE)

def setup_config(check_mode=False):
    # Handle config.conf
    if os.path.isdir(CONFIG_FILE):
        print_error(f"{CONFIG_FILE} exists but is a directory. Remove it and rerun bootstrap.")
        return False

    if not os.path.exists(CONFIG_FILE):
        if not os.path.exists(CONFIG_EXAMPLE):
            print_error(f"Missing example config: {CONFIG_EXAMPLE}")
            return False
        
        if check_mode:
            print_step(f"[DRY-RUN] Would create {CONFIG_FILE} from {CONFIG_EXAMPLE}")
        else:
            shutil.copyfile(CONFIG_EXAMPLE, CONFIG_FILE)
            print_step(f"Created {CONFIG_FILE} from {CONFIG_EXAMPLE}")
    else:
        print_step(f"{CONFIG_FILE} already exists. Skipping.")

    if not check_mode and not _harden_posix_mode(CONFIG_FILE, PRIVATE_FILE_MODE):
        return False

    # Handle .env
    if os.path.isdir(ENV_FILE):
        print_error(f"{ENV_FILE} exists but is a directory. Remove it and rerun bootstrap.")
        return False

    if not os.path.exists(ENV_FILE):
        if not os.path.exists(ENV_EXAMPLE):
            print_step(f"Optional: {ENV_EXAMPLE} not found. Skipping .env creation.")
        else:
            if check_mode:
                print_step(f"[DRY-RUN] Would create {ENV_FILE} from {ENV_EXAMPLE}")
            else:
                shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
                print_step(f"Created {ENV_FILE} from {ENV_EXAMPLE}")
    else:
        print_step(f"{ENV_FILE} already exists. Skipping.")

    if os.path.exists(ENV_FILE) and not check_mode:
        if not _harden_posix_mode(ENV_FILE, PRIVATE_FILE_MODE):
            return False

    return True

def run_install(check_mode=False):
    if check_mode:
        print_step("[DRY-RUN] Would run: poetry install")
        print_step("[DRY-RUN] Would run: poetry run playwright install chromium")
        return True

    print_step("Running: poetry install")
    try:
        subprocess.run(["poetry", "install"], check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"Poetry install failed with exit code {e.returncode}")
        return False

    print_step("Running: poetry run playwright install chromium")
    try:
        subprocess.run(["poetry", "run", "playwright", "install", "chromium"], check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"Playwright install failed with exit code {e.returncode}")
        return False

    # PR #2: Linux distribution detection and Arch-based note
    _, pretty_name, is_arch_based = get_linux_distro()
    if is_arch_based:
        print("\n" + "-" * 40)
        print(f"NOTE: Arch-based Linux detected ({pretty_name}).")
        print("\nPlaywright does not officially support Arch Linux distributions.")
        print("Using Ubuntu fallback Chromium builds is expected.")
        print("\nIf browser startup fails later, install the required system libraries using pacman.")
        print("See the Playwright Linux dependency documentation for details.")
        print("-" * 40)

    return True

def main():
    parser = argparse.ArgumentParser(description="Bootstrap WebAI-to-API development environment.")
    parser.add_argument("--no-install", action="store_true", help="Only create files/dirs, no poetry/playwright install")
    parser.add_argument("--check", action="store_true", help="Dry-run mode, report what would change")
    args = parser.parse_args()

    print("=" * 60)
    print("WebAI-to-API Bootstrap Utility")
    print("=" * 60)

    if not check_python_version():
        sys.exit(1)

    if not check_poetry():
        sys.exit(1)

    load_local_env()

    if not setup_config(args.check):
        sys.exit(1)

    if not setup_directories(args.check, get_configured_auth_state_dir()):
        sys.exit(1)

    if not setup_docker_runtime_source(args.check):
        sys.exit(1)

    if not args.no_install:
        if not run_install(args.check):
            sys.exit(1)

    if args.check:
        print("\n[CHECK COMPLETE] No changes were made.")
    else:
        print("\n" + "=" * 60)
        print("BOOTSTRAP COMPLETE")
        print("=" * 60)
        print("Next steps:")
        print("1. Run diagnostics: poetry run python scripts/doctor.py")
        print("2. Perform login:   poetry run python verify_login.py")
        print("3. Start server:    poetry run python src/run.py")
        print("=" * 60)

if __name__ == "__main__":
    main()
