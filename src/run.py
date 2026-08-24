# src/run.py
import argparse
import asyncio
import sys
import uvicorn
# --- App and Service Imports ---
from app.config import CONFIG, resolve_logging_config
from app.utils.startup import print_server_info, print_gemini_preflight_status


class ApplicationServer(uvicorn.Server):
    """
    Server-owned graceful shutdown.

    `_shutdown_intent_marked` guarantees application shutdown intent is
    recorded exactly once, before Uvicorn begins draining, regardless of how
    many signals or programmatic requests arrive.
    """

    _shutdown_intent_marked = False

    def _mark_application_shutdown_intent(self):
        """Mark runtime shutdown intent exactly once (idempotent)."""
        if self._shutdown_intent_marked:
            return
        self._shutdown_intent_marked = True
        from app.services.browser.engine import request_application_shutdown
        request_application_shutdown()

    def handle_exit(self, sig, frame):
        # Real signal path: mark intent first, then delegate unchanged so
        # Uvicorn keeps its own logging and second-signal force-exit behavior.
        self._mark_application_shutdown_intent()
        super().handle_exit(sig, frame)

    def request_shutdown(self, reason: str = "programmatic") -> bool:
        """
        Transport-independent programmatic graceful shutdown.

        Returns True only for the first accepted request. Rejected when the
        server has not started or Uvicorn already entered shutdown through
        another path (`should_exit` set) — such a later request must not be
        treated as a newly accepted shutdown. Uses Uvicorn's normal
        should_exit loop behavior: no fake signals, no direct Uvicorn
        handle_exit call, no engine teardown here.
        """
        if not getattr(self, "started", False) or self.should_exit:
            return False
        first_request = not self._shutdown_intent_marked
        self._mark_application_shutdown_intent()
        if not first_request:
            return False
        self.should_exit = True
        return True


def run_server(config):
    try:
        ApplicationServer(config).run()
    except KeyboardInterrupt:
        pass



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

    # Import app.main now that the root logger is configured
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
    config = uvicorn.Config(
        webai_app,
        host=args.host,
        port=args.port,
        reload=False,
        log_config=None,
        log_level=resolved_level.lower(),
        access_log=not resolved_disable_access,
        workers=1,
    )
    run_server(config)
