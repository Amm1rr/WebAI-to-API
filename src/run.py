# src/run.py
import argparse
import asyncio
import uvicorn
import multiprocessing
import time
import sys
import threading
import os
from typing import Dict, Union, Tuple
from fastapi.routing import APIRoute

# Import tomli to read pyproject.toml
try:
    import tomli
except ImportError:
    # For Python 3.11+, tomllib is in the standard library
    try:
        import tomllib as tomli
    except ImportError:
        tomli = None

# --- App and Service Imports ---
from app.config import load_config
from app.main import app as webai_app
from app.services.gemini_client import init_gemini_client

# Conditionally import g4f runner function
try:
    from g4f.api import run_api as run_g4f_api

    G4F_AVAILABLE = True
except ImportError:
    G4F_AVAILABLE = False


# Helper class for terminal colors
class Colors:
    """A class to hold ANSI color codes for terminal output."""

    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    RED = "\033[91m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


# --- Helper function to get app info ---
def get_app_info() -> Tuple[str, str]:
    """Reads application name and version from pyproject.toml."""
    if not tomli:
        return "WebAI to API", "N/A (tomli not installed)"
    try:
        with open("pyproject.toml", "rb") as f:
            toml_data = tomli.load(f)
        poetry_data = toml_data.get("tool", {}).get("poetry", {})
        name = poetry_data.get("name", "WebAI-to-API").replace("-", " ").title()
        version = poetry_data.get("version", "N/A")
        return name, version
    except (FileNotFoundError, KeyError):
        return "WebAI-to-API", "N/A"


# --- Server Runner Functions (to be run in separate processes) ---
def start_webai_server(host, port, reload):
    """Function to start the WebAI (Uvicorn) server."""
    # FIX: Set the policy for the child process on Windows
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print_server_info(host, port, "webai")
    uvicorn.run(webai_app, host=host, port=port, reload=reload, log_config=None)


def start_g4f_server(host, port):
    """Function to start the G4F server."""
    # FIX: Set the policy for the child process on Windows
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print_server_info(host, port, "g4f")
    run_g4f_api(host=host, port=port, proxy=None)


# --- Standard Input Listener ---
def input_listener(shared_state: Dict):
    """Listens for user input in a separate thread to avoid blocking."""
    while True:
        try:
            choice = input()
            if choice == "1":
                print(
                    f"\n[Controller] Input '1' received. Requesting to run {Colors.CYAN}WebAI mode{Colors.RESET}..."
                )
                shared_state["requested_mode"] = "webai"
            elif choice == "2":
                print(
                    f"\n[Controller] Input '2' received. Requesting to run {Colors.CYAN}G4F mode{Colors.RESET}..."
                )
                shared_state["requested_mode"] = "g4f"
        except (EOFError, KeyboardInterrupt):
            break


# --- Helper Function for Printing Info ---
def print_server_info(host: str, port: int, mode: str):
    """Displays complete, formatted information about the running server."""
    protocol = "http"
    base_url = f"{protocol}://{host}:{port}"
    app_name, app_version = get_app_info()
    app_info_line = f"{app_name} v{app_version}".center(80)
    print("\n" + "=" * 80)
    print(f"{Colors.BOLD}{Colors.YELLOW}{app_info_line}{Colors.RESET}")
    if mode == "webai":
        print("🚀 WebAI-to-API Server is RUNNING (Primary Mode) 🚀".center(80))
        print("=" * 80)
        print("\n✨ Available Services:")
        print(f"  - Docs (Swagger): {base_url}/docs")
        print("\n⚙️ Config.conf:")
        try:
            CONFIG = load_config()
            print(f"  - Browser: {CONFIG['Browser']['name']}")
            print(f"  - Model: {CONFIG['AI']['default_model_gemini']}")
        except Exception:
            print("  - Could not load config details.")
        print("\n🔗 API Endpoints:")
        paths = sorted(
            list(
                set(
                    route.path
                    for route in webai_app.routes
                    if isinstance(route, APIRoute)
                )
            )
        )
        for path in paths:
            if path.startswith("/") and path not in [
                "/docs",
                "/redoc",
                "/openapi.json",
            ]:
                print(f"  - {base_url}{path}")
    elif mode == "g4f":
        print("🚀 gpt4free Server is RUNNING 🚀".center(80))
        print("=" * 80)
        g4f_base_url = f"{base_url}/v1"
        print("\n✨ gpt4free Service Info:")
        print(f"  - Base URL: {g4f_base_url}")
        print(f"  - Docs (Swagger): {base_url}/docs")
        print("\n🔍 API Discovery Endpoints:")
        print(f"  - Models   : {g4f_base_url}/models")
        print(f"  - Providers: {g4f_base_url}/providers")
        print("\n🔗 Main API Endpoints:")
        print(f"  - Chat Completions: {g4f_base_url}/chat/completions")
        print(f"  - Image Generation: {g4f_base_url}/images/generate")
    print("\n" + "=" * 80)
    instruction_text = "Press '1' then Enter for WebAI (Faster) | '2' then Enter for gpt4free | Ctrl+C to Quit"
    colored_instructions = (
        f"{Colors.BOLD}{Colors.YELLOW}{instruction_text.center(80)}{Colors.RESET}"
    )
    print(colored_instructions)
    print("=" * 80)


# --- Main Execution Block ---
if __name__ == "__main__":
    # Fix: Set the asyncio event loop policy for Windows in the main process as well.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # This must be the first line inside the main block for multiprocessing on Windows.
    multiprocessing.freeze_support()
    # Fix: Move the multiprocessing Manager inside the main block.
    initial_data: Dict[str, Union[str, None]] = {"requested_mode": None}
    shared_state = multiprocessing.Manager().dict(initial_data)
    parser = argparse.ArgumentParser(
        description="Run a managed server with hot-switching capability."
    )
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=6969, help="Port number")
    parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reloading (for WebAI mode)"
    )
    args = parser.parse_args()
    # Step 1: Check availability of modes
    print("INFO:     Checking availability of server modes...")
    webai_is_available = asyncio.run(init_gemini_client())
    if webai_is_available:
        print(
            f"INFO:     ✅ {Colors.CYAN}WebAI-to-API mode is available{Colors.RESET} (Gemini client initialized)."
        )
    else:
        print(
            f"WARN:     ⚠️ {Colors.YELLOW}WebAI-to-API mode is not available{Colors.RESET} (Could not initialize Gemini client)."
        )
    if G4F_AVAILABLE:
        print(
            f"INFO:     ✅ {Colors.CYAN}gpt4free mode is available{Colors.RESET} ('g4f' library is installed)."
        )
    else:
        print(
            f"WARN:     ⚠️ {Colors.YELLOW}gpt4free mode is not available{Colors.RESET} ('g4f' library not found)."
        )

    # --- Set initial mode based on OS ---
    initial_mode = None
    is_windows = sys.platform == "win32"

    if is_windows:
        print("INFO:     Windows OS detected. Prioritizing G4F mode as default.")
        if G4F_AVAILABLE:
            initial_mode = "g4f"
        elif webai_is_available:
            initial_mode = "webai"
    else:  # For Linux, macOS, etc.
        print("INFO:     Non-Windows OS detected. Prioritizing WebAI mode as default.")
        if webai_is_available:
            initial_mode = "webai"
        elif G4F_AVAILABLE:
            initial_mode = "g4f"

    if not initial_mode:
        print("\nERROR:    No server modes are available to run. Exiting.")
        sys.exit(1)

    # Start background input listener thread
    # FIX: Pass the shared_state dictionary as an argument to the listener thread.
    input_thread = threading.Thread(
        target=input_listener, args=(shared_state,), daemon=True
    )
    input_thread.start()
    current_process = None
    current_mode = None
    try:
        # Main controller loop
        while True:
            requested = shared_state["requested_mode"]
            if not current_process or (requested and requested != current_mode):
                new_mode = requested or initial_mode
                if (new_mode == "webai" and not webai_is_available) or (
                    new_mode == "g4f" and not G4F_AVAILABLE
                ):
                    print(
                        f"\n[Controller] Cannot start '{Colors.YELLOW}{new_mode}{Colors.RESET}' mode: It is not available."
                    )
                    shared_state["requested_mode"] = None
                    continue
                if current_process and current_process.is_alive():
                    print(
                        f"\n[Controller] Stopping current server ('{Colors.CYAN}{current_mode}{Colors.RESET}')..."
                    )
                    current_process.terminate()
                    current_process.join(timeout=5)
                current_mode = new_mode
                shared_state["requested_mode"] = None
                print(
                    f"\n[Controller] Starting server in '{Colors.CYAN}{current_mode}{Colors.RESET}' mode..."
                )
                target_func, process_args = (
                    (start_webai_server, (args.host, args.port, args.reload))
                    if current_mode == "webai"
                    else (start_g4f_server, (args.host, args.port))
                )
                current_process = multiprocessing.Process(
                    target=target_func, args=process_args
                )
                current_process.start()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Controller] Ctrl+C detected. Shutting down all processes.")
    finally:
        # Final cleanup
        if current_process and current_process.is_alive():
            current_process.terminate()
            current_process.join()
        print("[Controller] Shutdown complete.")
        os._exit(0)
