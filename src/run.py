# src/run.py
import argparse
import asyncio
import logging
import os
import sys
import uvicorn
from typing import Tuple
from fastapi.routing import APIRoute

# --- App and Service Imports ---
from app.config import CONFIG
from app.utils.startup import print_server_info, print_gemini_preflight_status


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
    from app.logger import setup_logging
    setup_logging(resolved_level, resolved_disable_access)

    # 4. Import app.main now that the standard root logger handlers are set up
    from app.main import app as webai_app

    # Preflight gate: only start the server when Gemini is enabled in config.
    webai_is_available = CONFIG.getboolean("EnabledAI", "gemini", fallback=True)
    print_gemini_preflight_status(webai_is_available)
    if not webai_is_available:
        sys.exit(1)

    # Print server information summary banner
    default_model = CONFIG.get("Gemini", "default_model", fallback=None)
    print_server_info(args.host, args.port, "webai", default_model=default_model)

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
