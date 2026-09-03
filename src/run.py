# src/run.py
import argparse
import asyncio
import logging
import os
import signal
import sys
import threading

import uvicorn

logger = logging.getLogger("app")

from app.shutdown import request_shutdown as request_generic_shutdown
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
        self._startup_shutdown_requested = False

    def _log_started_message(self, listeners):
        # Defensive: suppress log if shutdown was requested during startup
        # after listener creation race. Primary prevention is in startup()
        # via lifespan.should_exit before listener creation.
        if self.should_exit:
            return
        super()._log_started_message(listeners)

    async def startup(self, sockets=None):
        # Covers the narrow case where SIGINT arrived after signal handlers
        # became active but before ApplicationServer.startup() began.
        if self._startup_shutdown_requested:
            lifespan = getattr(self, "lifespan", None)
            if lifespan is not None:
                lifespan.should_exit = True

        await super().startup(sockets=sockets)

        if self._startup_shutdown_requested:
            if self.started:
                # Narrow race: SIGINT arrived after Uvicorn's lifespan.should_exit
                # check, so listeners may already have been created.
                # Use Uvicorn's own shutdown implementation to close them and
                # execute lifespan shutdown.
                await super().shutdown(sockets=sockets)
            elif not self.force_exit:
                # Normal startup-SIGINT case: Uvicorn observed lifespan.should_exit
                # and returned before listener creation, but Server._serve will
                # return without calling shutdown(), so complete lifespan shutdown
                # here.
                await self.lifespan.shutdown()
            return

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
        # Generic application intent (neutral, for ASGI boundary)
        request_generic_shutdown("application")
        # BrowserEngine intent remains authoritative for browser lifecycle
        from app.services.browser.engine import request_application_shutdown

        request_application_shutdown()

    def _mark_startup_shutdown(self):
        """Mark that shutdown was requested before server.started became True."""
        self._startup_shutdown_requested = True
        # Use lifespan boundary to prevent normal listener creation.
        # When lifespan object exists, set its should_exit so startup returns
        # before listeners. This is safe to set even if lifespan already completed;
        # startup() will check self.should_exit as well.
        lifespan = getattr(self, "lifespan", None)
        if lifespan is not None:
            try:
                lifespan.should_exit = True
            except Exception:
                pass

    def handle_exit(self, sig, frame):
        # Emergency hard exit: second SIGINT while shutdown already active
        # must terminate immediately with POSIX 130 without delegating to
        # Uvicorn's force-exit machinery.
        # This is the operator force-quit path; first SIGINT and SIGTERM
        # retain normal graceful shutdown.
        should_hard_exit = False
        with self._shutdown_lock:
            if self.should_exit and sig == signal.SIGINT:
                should_hard_exit = True
            else:
                self._mark_application_shutdown_intent()
                if not self.started:
                    self._mark_startup_shutdown()
        if should_hard_exit:
            logger.warning("Emergency shutdown: second SIGINT received, exiting immediately.")
            os._exit(130)
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
        timeout_graceful_shutdown=15,
    )
    run_server(config)
