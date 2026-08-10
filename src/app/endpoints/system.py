from fastapi import APIRouter, Response, status
from typing import Optional
from app.services.browser.engine import BrowserEngine
from app.services.browser.auth_manager import get_auth_manager

router = APIRouter(tags=["System"])

def get_existing_browser_engine() -> Optional[BrowserEngine]:
    """
    Non-initializing access to the BrowserEngine singleton.
    Safe for liveness probes to avoid triggering bootstrap.
    """
    return BrowserEngine._instance

@router.get(
    "/health",
    summary="Liveness Probe",
    description=(
        "Standard liveness check. Returns 200 if the application process is alive and "
        "the BrowserEngine is not in a terminal shutdown state. This endpoint is "
        "strictly side-effect-free and does not trigger browser initialization or recovery."
    ),
    responses={
        200: {"description": "Application is healthy and running."},
        503: {"description": "Application is in terminal shutdown state."}
    }
)
async def health():
    engine = get_existing_browser_engine()
    # If engine doesn't exist yet, it's 'alive' (it just hasn't been used)
    if engine and engine.is_shutting_down:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(status_code=status.HTTP_200_OK)

@router.get(
    "/ready",
    summary="Readiness Probe",
    description=(
        "Standard readiness check. Returns 200 only if the structural runtime is fully "
        "initialized and capable of accepting work. This includes verifying the browser "
        "process connectivity and session liveness. This endpoint is side-effect-free, "
        "does not validate authentication, and does not trigger recovery logic."
    ),
    responses={
        200: {"description": "Runtime is ready to accept requests."},
        503: {"description": "Runtime is not initialized, browser is disconnected, or no sessions are alive."}
    }
)
async def ready():
    engine = get_existing_browser_engine()
    
    # 1. If engine isn't initialized, we aren't structurally ready
    if not engine:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    # 2. Engine must not be shutting down
    if engine.is_shutting_down:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        
    # 3. Browser must be connected
    if not engine.browser or not engine.browser.is_connected():
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        
    # 4. At least one session must be structurally alive
    # Lock-free check of existing sessions
    has_alive_session = False
    for session in engine.sessions.values():
        if session.is_alive:
            has_alive_session = True
            break
            
    if not has_alive_session:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        
    return Response(status_code=status.HTTP_200_OK)

@router.get(
    "/v1/runtime/status",
    summary="Runtime Diagnostics",
    description=(
        "Returns a detailed diagnostic payload regarding the internal state of the "
        "hardened browser runtime. Includes engine status, browser generation, lease "
        "usage, and a cached summary of authentication status. This endpoint is "
        "strictly side-effect-free and does not refresh authentication or trigger recovery."
    )
)
async def runtime_status():
    engine = get_existing_browser_engine()
    auth_mgr = get_auth_manager()
    
    if not engine:
        return {
            "engine": {"status": "NOT_INITIALIZED"},
            "auth": auth_mgr.get_status()
        }

    # Side-effect free collection
    status_payload = {
        "engine": {
            "status": "SHUTTING_DOWN" if engine.is_shutting_down else "RUNNING",
            "browser_connected": engine.browser.is_connected() if engine.browser else False,
            "browser_generation": engine.browser_generation,
            "is_bootstrap": engine.is_bootstrap
        },
        "sessions": {},
        "auth": auth_mgr.get_status() # Cached only
    }
    
    for name, session in engine.sessions.items():
        status_payload["sessions"][name] = {
            "is_alive": session.is_alive,
            "metrics": session.metrics,
            "is_recovering": session._recovery_task is not None
        }
        
    return status_payload
