import os
import sys
import socket
import json
import configparser
import subprocess
from pathlib import Path

# Colors
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_status(label, status, message="", color=Colors.ENDC):
    print(f"{Colors.BOLD}{label:<20}{Colors.ENDC} [{color}{status:<7}{Colors.ENDC}] {message}")

def check_config():
    if not os.path.exists("config.conf"):
        print_status("Configuration", "FAIL", "config.conf is missing. Run: python scripts/bootstrap.py", Colors.FAIL)
        return False, None
    
    try:
        config = configparser.ConfigParser()
        config.read("config.conf", encoding="utf-8")
        print_status("Configuration", "PASS", "config.conf found")
        return True, config
    except Exception as e:
        print_status("Configuration", "FAIL", f"Error reading config.conf: {e}", Colors.FAIL)
        return False, None

def check_runtime_dirs():
    dirs = ["runtime", "runtime/auth", "runtime/cache", "runtime/logs", "runtime/conversations"]
    missing = [d for d in dirs if not os.path.isdir(d)]
    
    if not missing:
        print_status("Directories", "PASS", "Runtime directory structure is correct")
        return True
    else:
        print_status("Directories", "FAIL", f"Missing: {', '.join(missing)}", Colors.FAIL)
        return False

def check_playwright():
    # Check if playwright package is installed via poetry
    try:
        res = subprocess.run(["poetry", "run", "python", "-c", "import playwright; print('ok')"], 
                             capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            print_status("Playwright Pkg", "PASS", "Playwright package is installed")
        else:
            print_status("Playwright Pkg", "FAIL", "Playwright package not found. Run: poetry install", Colors.FAIL)
            return False
    except Exception as e:
        print_status("Playwright Pkg", "FAIL", f"Could not check playwright: {e}", Colors.FAIL)
        return False

    # Check for chromium binaries using a lightweight script.
    # Doctor intentionally performs a lightweight, side-effect-free check and does not launch Chromium.
    try:
        # Check if the executable exists via playwright internal path resolver
        check_script = (
            "import asyncio; from playwright.async_api import async_playwright; "
            "async def run():\n"
            "  async with async_playwright() as p:\n"
            "    try:\n"
            "      executable = p.chromium.executable_path\n"
            "      import os; print('ok' if os.path.exists(executable) else 'missing')\n"
            "    except Exception as e:\n"
            "      print(f'error:{e}')\n"
            "asyncio.run(run())"
        )
        res = subprocess.run(["poetry", "run", "python", "-c", check_script], 
                             capture_output=True, text=True, timeout=10)
        
        output = res.stdout.strip()
        if "ok" in output:
            print_status("Chromium Bin", "PASS", "Chromium binaries found")
            return True
        elif "missing" in output:
            print_status("Chromium Bin", "FAIL", "Chromium binaries missing. Run: poetry run playwright install chromium", Colors.FAIL)
            return False
        else:
            # If we got an error or unexpected output, we can't be 100% sure
            print_status("Chromium Bin", "WARN", "Unable to determine Chromium status reliably. Run: poetry run playwright install chromium", Colors.WARNING)
            return True  # WARN doesn't fail the whole doctor run
    except Exception as e:
        print_status("Chromium Bin", "WARN", f"Check failed: {e}. Run: poetry run playwright install chromium", Colors.WARNING)
        return True

def check_auth_material(config):
    has_fail = False
    
    # Check config cookies
    psid = config.get("Gemini", "__Secure-1PSID", fallback="")
    psidts = config.get("Gemini", "__Secure-1PSIDTS", fallback="")
    
    if psid and psidts:
        print_status("Auth (Config)", "PASS", "Gemini cookies found in config.conf")
    else:
        # Check legacy section
        psid_l = config.get("Cookies", "gemini_cookie_1psid", fallback="")
        psidts_l = config.get("Cookies", "gemini_cookie_1psidts", fallback="")
        if psid_l and psidts_l:
            print_status("Auth (Config)", "WARN", "Using legacy [Cookies] section. Please migrate to [Gemini].", Colors.WARNING)
        else:
            print_status("Auth (Config)", "WARN", "No Gemini cookies in config.conf", Colors.WARNING)

    # Check runtime/auth/gemini.json
    json_path = "runtime/auth/gemini.json"
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and "cookies" in data:
                    print_status("Auth (JSON)", "PASS", "runtime/auth/gemini.json exists and is valid")
                else:
                    print_status("Auth (JSON)", "FAIL", "runtime/auth/gemini.json is invalid format", Colors.FAIL)
                    has_fail = True
        except Exception as e:
            print_status("Auth (JSON)", "FAIL", f"Error reading gemini.json: {e}", Colors.FAIL)
            has_fail = True
    else:
        print_status("Auth (JSON)", "WARN", "runtime/auth/gemini.json missing. Run: python verify_login.py", Colors.WARNING)

    return not has_fail

def check_port():
    port = 6969
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        print_status("Port 6969", "PASS", "Port is available")
        return True
    except socket.error:
        print_status("Port 6969", "FAIL", "Port 6969 is already in use", Colors.FAIL)
        return False
    finally:
        s.close()

def check_exposure():
    # In doctor.py we don't have full access to current running process args, 
    # but we can check config if it overrides defaults, although current app uses CLI args for host.
    # We'll just check common "unsafe" binds if we could.
    # For now, let's just note that localhost is the safe default.
    print_status("Security", "INFO", "Dashboard is safe when bound to localhost (default)")
    return True

def main():
    print("=" * 60)
    print("WebAI-to-API Diagnostics (Doctor)")
    print("=" * 60)

    has_fail = False

    config_ok, config = check_config()
    if not config_ok: has_fail = True

    if not check_runtime_dirs(): has_fail = True
    
    # Only check playwright if we have config
    if config_ok:
        if not check_playwright(): has_fail = True
        if not check_auth_material(config): has_fail = True

    if not check_port(): has_fail = True
    
    check_exposure()

    print("=" * 60)
    if has_fail:
        print(f"{Colors.FAIL}{Colors.BOLD}DIAGNOSTICS FAILED{Colors.ENDC}")
        print("Please address the FAIL items above.")
        sys.exit(1)
    else:
        print(f"{Colors.OKGREEN}{Colors.BOLD}DIAGNOSTICS PASSED{Colors.ENDC}")
        print("Your environment looks good!")
        sys.exit(0)

if __name__ == "__main__":
    main()
