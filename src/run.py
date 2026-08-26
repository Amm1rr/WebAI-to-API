# src/run.py
import argparse
import asyncio
import os
import sys
import threading

import uvicorn
# --- App and Service Imports ---
from app.config import CONFIG, get_runtime_dir, resolve_logging_config
from app.utils.startup import (
    configure_startup_output,
    print_gemini_preflight_status,
    print_server_info,
)


def configure_windows_event_loop_policy():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


class ApplicationServer(uvicorn.Server):
    """
    Server-owned graceful shutdown.

    `_shutdown_intent_marked` guarantees application shutdown intent is
    recorded exactly once, before Uvicorn begins draining, regardless of how
    many signals or programmatic requests arrive.

    Shutdown state is guarded by a per-instance lock: POSIX signal handlers
    (main thread) and the Windows IPC listener thread may race on the
    check/set transition. The lock only protects state transitions; no
    long-running teardown runs while it is held.
    """

    def __init__(self, config):
        super().__init__(config)
        self._shutdown_lock = threading.Lock()
        self._shutdown_intent_marked = False
        self._update_check_task = None

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if (
            self.started
            and self._update_check_task is None
            and CONFIG.getboolean("General", "check_updates", fallback=True)
        ):
            from app.utils.update_check import run_update_check

            self._update_check_task = asyncio.create_task(
                run_update_check(), name="webai-update-check"
            )

    async def shutdown(self, sockets=None):
        task = self._update_check_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().shutdown(sockets=sockets)

    def _mark_application_shutdown_intent(self):
        """Mark runtime shutdown intent exactly once (idempotent)."""
        if self._shutdown_intent_marked:
            return
        self._shutdown_intent_marked = True
        from app.services.browser.engine import request_application_shutdown
        request_application_shutdown()

    def handle_exit(self, sig, frame):
        # Real signal path: mark intent first (at most once, under the same
        # lock as the programmatic path), then delegate unchanged so Uvicorn
        # keeps its own logging and second-signal force-exit behavior.
        with self._shutdown_lock:
            self._mark_application_shutdown_intent()
        super().handle_exit(sig, frame)

    def request_shutdown(self, reason: str = "programmatic") -> bool:
        """
        Transport-independent programmatic graceful shutdown.

        Returns True only for the first accepted request. Rejected when the
        server has not started or Uvicorn already entered shutdown through
        another path (`should_exit` set) — such a later request must not be
        treated as a newly accepted shutdown. The full check/set transition
        is atomic under `_shutdown_lock`, so concurrent requests yield
        exactly one True. Uses Uvicorn's normal should_exit loop behavior:
        no fake signals, no direct Uvicorn handle_exit call, no engine
        teardown here.
        """
        with self._shutdown_lock:
            if not getattr(self, "started", False) or self.should_exit:
                return False
            first_request = not self._shutdown_intent_marked
            self._mark_application_shutdown_intent()
            if not first_request:
                return False
            self.should_exit = True
            return True


def run_server(config):
    server = ApplicationServer(config)
    listener = None
    if sys.platform == "win32":
        from app.shutdown_transport import ShutdownListener

        control_file = os.path.join(
            get_runtime_dir(), "shutdown-control.json"
        )
        listener = ShutdownListener(
            callback=server.request_shutdown,
            control_file=control_file,
        )
        listener.start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        if listener is not None:
            listener.stop()



# --- Main Execution Block ---
if __name__ == "__main__":
    # Fix: Set the asyncio event loop policy for Windows.
    configure_windows_event_loop_policy()
    configure_startup_output()

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
