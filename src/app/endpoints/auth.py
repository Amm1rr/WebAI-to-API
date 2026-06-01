# src/app/endpoints/auth.py
from fastapi import APIRouter, HTTPException, Query
from app.services.browser.auth_manager import get_auth_manager
from app.logger import logger

router = APIRouter(prefix="/v1/auth", tags=["Authentication"])

@router.get(
    "/status",
    summary="Get Authentication Status",
    description="Inspects the current authentication state. Returns information about whether providers are logged in, pending login operations, and runtime auth diagnostics."
)
async def get_auth_status(refresh: bool = Query(False, description="Force a lightweight cache refresh")):
    auth_mgr = get_auth_manager()
    if refresh:
        try:
            auth_mgr.refresh_status()
        except Exception as e:
            logger.warning(f"Error during manual status refresh: {e}")
    
    return auth_mgr.get_status()

@router.post(
    "/login",
    status_code=202,
    summary="Trigger Authentication Login",
    description="Starts an isolated browser-based login workflow. Opens the authentication flow for the user to log in when sessions expire."
)
async def trigger_auth_login():
    auth_mgr = get_auth_manager()
    try:
        auth_mgr.start_login()
        return {
            "status": "LOGIN_IN_PROGRESS",
            "message": "Isolated login workflow triggered. Poll the status endpoint to monitor progress."
        }
    except ValueError as e:
        # A login is already running (concurrency lock active)
        raise HTTPException(
            status_code=409,
            detail=str(e)
        )
    except RuntimeError as e:
        # Unsupported environment (e.g., headless Docker with no display)
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error triggering login workflow: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger login workflow: {str(e)}"
        )
