# src/app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

from app.services.gemini_client import init_gemini_client, GeminiClientNotInitializedError
from app.services.session_manager import init_session_managers
from app.logger import logger

# Import endpoint routers
from app.endpoints import gemini, chat, google_generative

import os
import signal
import threading
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Initializes services on startup.
    """
    import time
    
    def get_log_payload():
        return f"PID: {os.getpid()}, PPID: {os.getppid()}, Thread: {threading.current_thread().name}, Time: {time.time()} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())})"

    # 1. First line of lifespan startup
    logger.info(f"[LIFESPAN-DEBUG] [STARTUP-START] {get_log_payload()}")

    # D & E: Log PID, parent PID, PGID, SID, and whether running as PID 1
    pid = os.getpid()
    ppid = os.getppid()
    pgid = os.getpgid(0)
    sid = os.getsid(0)
    is_pid_1 = (pid == 1)
    
    # A: FastAPI lifespan startup log
    logger.info(
        f"[LIFESPAN-STARTUP] Process Topology:\n"
        f"PID: {pid}\n"
        f"PPID: {ppid}\n"
        f"PGID: {pgid}\n"
        f"SID: {sid}\n"
        f"Is PID 1: {is_pid_1}\n"
        f"Thread Name: {threading.current_thread().name}"
    )

    # 5. Signal getsignal diagnostics
    logger.info(f"[SHUTDOWN-DEBUG] [SIGNAL-GET] Current SIGTERM handler: {signal.getsignal(signal.SIGTERM)}")
    logger.info(f"[SHUTDOWN-DEBUG] [SIGNAL-GET] Current SIGINT handler: {signal.getsignal(signal.SIGINT)}")

    # C: Register temporary SIGTERM/SIGINT debug handlers with chaining
    original_sigterm = None
    original_sigint = None

    def debug_signal_handler(signum, frame):
        curr_pid = os.getpid()
        curr_ppid = os.getppid()
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        
        loop_info = "N/A"
        try:
            loop = asyncio.get_running_loop()
            loop_info = repr(loop)
        except RuntimeError:
            loop_info = "No active running event loop available in this thread context."
            
        logger.info(
            f"[SHUTDOWN-DEBUG] [SIGNAL-RECEIVED] Received signal {sig_name} ({signum}). "
            f"PID: {curr_pid}, PPID: {curr_ppid}, Thread: {threading.current_thread().name}, "
            f"Time: {time.time()} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}), Loop: {loop_info}"
        )
        if signum == signal.SIGTERM and original_sigterm:
            if callable(original_sigterm):
                original_sigterm(signum, frame)
        elif signum == signal.SIGINT and original_sigint:
            if callable(original_sigint):
                original_sigint(signum, frame)

    try:
        original_sigterm = signal.signal(signal.SIGTERM, debug_signal_handler)
        original_sigint = signal.signal(signal.SIGINT, debug_signal_handler)
        logger.info("[SHUTDOWN-DEBUG] Chained SIGTERM and SIGINT handlers registered successfully.")
    except ValueError as e:
        logger.warning(f"[SHUTDOWN-DEBUG] Could not register signal handlers (likely not in main thread): {e}")

    # Initialize Gemini client in server process
    init_result = await init_gemini_client()
    if init_result:
        logger.info("Gemini client successfully initialized in server process.")
    else:
        logger.error("Failed to initialize Gemini client in server process.")

    # Initialize session managers
    try:
        init_session_managers()
        logger.info("Session managers initialized for WebAI-to-API.")
    except GeminiClientNotInitializedError as e:
        logger.warning(f"Session managers not initialized: {e}")

    # 1. Immediately before yield
    logger.info(f"[LIFESPAN-DEBUG] [STARTUP-BEFORE-YIELD] {get_log_payload()}")

    yield

    # 1. Immediately after yield resumes
    logger.info(f"[LIFESPAN-DEBUG] [SHUTDOWN-AFTER-YIELD-RESUME] {get_log_payload()}")

    # 1. Very start of shutdown section
    logger.info(f"[LIFESPAN-DEBUG] [SHUTDOWN-START] {get_log_payload()}")

    # B: FastAPI lifespan shutdown log
    curr_pid = os.getpid()
    curr_ppid = os.getppid()
    logger.info(
        f"[LIFESPAN-SHUTDOWN] FastAPI application lifespan shutdown executing... "
        f"PID: {curr_pid}, PPID: {curr_ppid}, Is PID 1: {curr_pid == 1}"
    )

    try:
        tasks = asyncio.all_tasks()
        curr_task = asyncio.current_task()
        logger.info(f"[SHUTDOWN-DEBUG] [TASK-DUMP] Total asyncio tasks: {len(tasks)}")
        
        for idx, task in enumerate(tasks):
            if task is curr_task:
                continue
            cancelling_val = getattr(task, "cancelling", lambda: "N/A")() if hasattr(task, "cancelling") else "N/A"
            logger.info(
                f"[SHUTDOWN-DEBUG] [TASK-DUMP] Task {idx + 1}:\n"
                f"name={task.get_name()}\n"
                f"done={task.done()}\n"
                f"cancelled={task.cancelled()}\n"
                f"cancelling={cancelling_val}\n"
                f"repr={repr(task)}"
            )
    except Exception as e:
        logger.error(f"[SHUTDOWN-DEBUG] Error during task inspection: {e}", exc_info=True)

    # Shutdown logic
    logger.info("Gracefully closing BrowserEngine during application shutdown...")
    try:
        from app.services.browser.engine import get_browser_engine
        engine = await get_browser_engine()
        await engine.close()
        logger.info("BrowserEngine closed gracefully.")
    except Exception as e:
        logger.error(f"Error closing BrowserEngine: {e}", exc_info=True)
    logger.info("Application shutdown complete.")

    # 1. Very end of shutdown section
    logger.info(f"[LIFESPAN-DEBUG] [SHUTDOWN-END] {get_log_payload()}")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the endpoint routers for WebAI-to-API
app.include_router(gemini.router)
app.include_router(chat.router)
app.include_router(google_generative.router)
