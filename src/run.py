# src/run.py
import argparse
import asyncio
import logging
import sys
import uvicorn
from typing import Tuple
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
        print("WebAI-to-API Server is RUNNING".center(80))
        print("=" * 80)
        print("\n✨ Available Services:")
        print(f"  - Dashboard:      {base_url}/ui")
        print(f"  - Docs (Swagger): {base_url}/docs")
        print("\n⚙️ Config.conf:")
        try:
            CONFIG = load_config()
            print(f"  - Default Model: {CONFIG['Gemini']['default_model']}")
        except Exception:
            print("  - Could not load config details.")
        print("\n🔗 Primary APIs:")
        print(f"  - POST {base_url}/v1/chat/completions")
        print(f"  - POST {base_url}/translate")
        print(f"  - POST {base_url}/v1beta/models/{{model_path:path}}")
        print("\n🔗 Useful Endpoints:")
        print(f"  - GET  {base_url}/v1/models")
        print(f"  - GET  {base_url}/v1/auth/status")
        print(f"  - POST {base_url}/v1/auth/login")
    print("\n" + "=" * 80)
    instruction_text = "Press Ctrl+C to Quit"
    colored_instructions = (
        f"{Colors.BOLD}{Colors.YELLOW}{instruction_text.center(80)}{Colors.RESET}"
    )
    print(colored_instructions)
    print("=" * 80)


# --- Main Execution Block ---
if __name__ == "__main__":
    # Fix: Set the asyncio event loop policy for Windows.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(
        description="Run the WebAI-to-API server."
    )
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=6969, help="Port number")
    parser.add_argument("--log-level", type=str, default="debug", help="Logging level")
    args = parser.parse_args()

    # Sync root logger level with CLI argument
    logging.getLogger().setLevel(args.log_level.upper())

    print("INFO:     Checking Gemini service availability...")
    # Preflight gate: only start the server when Gemini is enabled in config.
    webai_is_available = CONFIG.getboolean("EnabledAI", "gemini", fallback=True)
    if webai_is_available:
        print(
            f"INFO:     ✅ {Colors.CYAN}Gemini service is enabled; starting WebAI-to-API server.{Colors.RESET}"
        )
    else:
        print(
            f"WARN:     ⚠️ {Colors.YELLOW}WebAI-to-API mode is not available{Colors.RESET} (Gemini is disabled in config)."
        )

    if not webai_is_available:
        print("\nERROR:    Gemini service is disabled. Exiting.")
        sys.exit(1)

    # Print server information summary banner
    print_server_info(args.host, args.port, "webai")

    # Run the Uvicorn server directly in the main thread
    uvicorn.run(
        webai_app,
        host=args.host,
        port=args.port,
        reload=False,
        log_config=None,
        log_level=args.log_level,
        workers=1,
    )
