# src/run.py
import argparse
import asyncio
import uvicorn
import multiprocessing
import time
import sys
import threading
import os
import signal
from typing import Dict, Union, Tuple
from fastapi.routing import APIRoute
from typing import TYPE_CHECKING

# This block is only processed by type checkers like Pylance
if TYPE_CHECKING:
    from multiprocessing.synchronize import Event as MultiprocessingEvent

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
from app.config import load_config, CONFIG
from app.main import app as webai_app

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


# --- UNIFIED Server Runner Functions ---


def start_webai_server(
    host: str, port: int, stop_event: "MultiprocessingEvent"
):
    """Starts the WebAI Uvicorn server with a graceful shutdown mechanism."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    config = uvicorn.Config(
        webai_app, host=host, port=port, reload=False, log_config=None, workers=1
    )
    server = uvicorn.Server(config)

    def shutdown_monitor():
        stop_event.wait()
        server.should_exit = True

    monitor_thread = threading.Thread(target=shutdown_monitor, daemon=True)
    monitor_thread.start()

    print_server_info(host, port, "webai")
    server.run()
    print(f"\n[WebAI Server] Process exited gracefully.")


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
        print("WebAI-to-API Server is RUNNING (Primary Mode)".center(80))
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
    print("\n" + "=" * 80)
    instruction_text = "Press Ctrl+C to Quit"
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

    parser = argparse.ArgumentParser(
        description="Run a managed server with WebAI capabilities."
    )
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=6969, help="Port number")
    args = parser.parse_args()

    print("INFO:     Checking availability of server modes...")
    # Simple check: assume WebAI is available if Gemini is enabled in config
    webai_is_available = CONFIG.getboolean("EnabledAI", "gemini", fallback=True)
    if webai_is_available:
        print(
            f"INFO:     ✅ {Colors.CYAN}WebAI-to-API mode is available{Colors.RESET} (Gemini client will be initialized on startup)."
        )
    else:
        print(
            f"WARN:     ⚠️ {Colors.YELLOW}WebAI-to-API mode is not available{Colors.RESET} (Gemini is disabled in config)."
        )

    if not webai_is_available:
        print("\nERROR:    No server modes are available to run. Exiting.")
        sys.exit(1)

    print(f"\n[Controller] Starting server in 'webai' mode...")

    stop_event = multiprocessing.Event()
    current_process = multiprocessing.Process(
        target=start_webai_server, args=(args.host, args.port, stop_event)
    )
    current_process.start()

    try:
        # Keep main thread alive while subprocess is running
        while current_process.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Controller] Ctrl+C detected. Initiating final shutdown...")
    finally:
        # Final cleanup
        if stop_event and not stop_event.is_set():
            stop_event.set()

        if current_process and current_process.is_alive():
            print("[Controller] Waiting for final server process to shut down...")
            current_process.join(timeout=10)
            if current_process.is_alive():
                print("[Controller] Server did not shut down gracefully, terminating.")
                current_process.terminate()

        print("[Controller] Shutdown complete. Forcing exit.")
        os._exit(0)
