# src/run.py
import argparse
import asyncio
import logging
import os
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


def _read_project_metadata() -> Tuple[str, str]:
    """Read the project name and version from pyproject metadata."""
    if not tomli:
        return "WebAI to API", "N/A (tomli not installed)"

    try:
        with open("pyproject.toml", "rb") as f:
            toml_data = tomli.load(f)

        project_data = toml_data.get("project", {})
        poetry_data = toml_data.get("tool", {}).get("poetry", {})

        name = project_data.get("name") or poetry_data.get("name", "WebAI-to-API")
        version = project_data.get("version") or poetry_data.get("version", "N/A")
        return name.replace("-", " ").title(), version
    except (FileNotFoundError, KeyError, TypeError):
        return "WebAI-to-API", "N/A"


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
    return _read_project_metadata()


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


def setup_logging(log_level: str, disable_access_logs: bool) -> None:
    """Configures the root logger, overrides verbose loggers, and bridges Loguru to standard logging."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Initialize the Python standard root logger
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)]
    )
    logging.getLogger().setLevel(numeric_level)

    # Prevent verbose logs from third-party libraries/HTTP clients from flooding the console
    logging.getLogger("httpx").setLevel(logging.DEBUG if log_level.upper() in ("DEBUG", "TRACE") else logging.WARNING)

    # Bridge Loguru (gemini_webapi) logs into standard logging via a function sink
    try:
        from loguru import logger as loguru_logger

        def loguru_sink(message):
            record = message.record
            name = record["extra"].get("name", record["name"])
            level_no = record["level"].no
            msg = record["message"]
            logging.getLogger(name).log(level_no, msg)

        loguru_logger.remove()  # Remove Loguru default handler
        loguru_logger.add(loguru_sink, level=0)  # Route all Loguru logs to Python logging
    except ImportError:
        pass


def resolve_logging_config(cli_log_level: str | None, cli_disable_access_logs: bool) -> Tuple[str, bool]:
    """Resolves log level and access log settings based on CLI, env, and config precedence."""
    # 1. Resolve log level precedence: explicit CLI > LOG_LEVEL env > config.conf > INFO
    env_log_level = os.environ.get("LOG_LEVEL")
    conf_log_level = CONFIG.get("Logging", "level", fallback=None) if CONFIG.has_section("Logging") else None
    resolved_level = cli_log_level or env_log_level or conf_log_level or "INFO"

    # 2. Resolve access logs precedence: explicit CLI --disable-access-logs > DISABLE_ACCESS_LOGS env > config.conf > false
    env_disable_access = os.environ.get("DISABLE_ACCESS_LOGS", "false").lower() in ("true", "1", "yes", "on")
    conf_disable_access = CONFIG.getboolean("Logging", "disable_access_logs", fallback=False) if CONFIG.has_section("Logging") else False
    resolved_disable_access = cli_disable_access_logs or env_disable_access or conf_disable_access

    return resolved_level, resolved_disable_access


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
    parser.add_argument("--log-level", type=str, default=None, help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    parser.add_argument("--disable-access-logs", action="store_true", help="Disable HTTP access logs")
    args = parser.parse_args()

    # Resolve configuration options
    resolved_level, resolved_disable_access = resolve_logging_config(args.log_level, args.disable_access_logs)

    # Setup logging
    setup_logging(resolved_level, resolved_disable_access)

    # 4. Import app.main now that the standard root logger handlers are set up
    from app.main import app as webai_app

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
        log_level=resolved_level.lower(),
        access_log=not resolved_disable_access,
        workers=1,
    )
