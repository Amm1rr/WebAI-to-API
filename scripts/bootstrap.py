import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path

# Constants
REQUIRED_PYTHON_VERSION = (3, 10)
MAX_PYTHON_VERSION = (3, 13)
CONFIG_FILE = "config.conf"
CONFIG_EXAMPLE = "config.conf.example"
RUNTIME_DIRS = [
    "runtime",
    "runtime/auth",
    "runtime/cache",
    "runtime/logs",
    "runtime/conversations",
]

def print_step(message):
    print(f"--> {message}")

def print_error(message):
    print(f"ERROR: {message}", file=sys.stderr)

def check_python_version():
    current_version = sys.version_info[:2]
    if current_version < REQUIRED_PYTHON_VERSION:
        print_error(f"Python version {REQUIRED_PYTHON_VERSION[0]}.{REQUIRED_PYTHON_VERSION[1]} or newer is required.")
        print_error(f"Current version is {current_version[0]}.{current_version[1]}.")
        return False
    
    if current_version >= MAX_PYTHON_VERSION:
        print(f"--> WARNING: Python {current_version[0]}.{current_version[1]} is newer than the tested range ({REQUIRED_PYTHON_VERSION[0]}.{REQUIRED_PYTHON_VERSION[1]} to {MAX_PYTHON_VERSION[0]}.{MAX_PYTHON_VERSION[1]-1}); continuing.")
    
    return True

def check_poetry():
    poetry_path = shutil.which("poetry")
    if not poetry_path:
        print_error("Poetry not found in PATH.")
        print_error("Please install Poetry following the official instructions: https://python-poetry.org/docs/#installation")
        return False
    return True

def setup_directories(check_mode=False):
    for dir_path in RUNTIME_DIRS:
        if not os.path.exists(dir_path):
            if check_mode:
                print_step(f"[DRY-RUN] Would create directory: {dir_path}")
            else:
                os.makedirs(dir_path, exist_ok=True)
                print_step(f"Created directory: {dir_path}")
        else:
            print_step(f"Directory already exists: {dir_path}")

def setup_config(check_mode=False):
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

    setup_directories(args.check)
    
    if not setup_config(args.check):
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
        print("1. Run diagnostics: python scripts/doctor.py")
        print("2. Perform login:   python verify_login.py")
        print("3. Start server:    python src/run.py")
        print("=" * 60)

if __name__ == "__main__":
    main()
