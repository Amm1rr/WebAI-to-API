from pathlib import Path
from typing import Any
from mimetypes import guess_type

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app.endpoints.auth import get_auth_status
from app.endpoints.chat import list_models
from app.endpoints.system import runtime_status


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
STATIC_DIR = Path(__file__).resolve().parents[1] / "static" / "ui"

router = APIRouter(prefix="/ui", tags=["Dashboard"], include_in_schema=False)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _template_context(request: Request, **values: Any) -> dict[str, Any]:
    context = {"request": request}
    context.update(values)
    return context


class DashboardStaticApp:
    """
    Minimal mounted static asset app for the bundled dashboard assets.

    The dashboard only serves small, version-controlled CSS/JS files. Keeping
    this path buffered avoids StaticFiles/FileResponse issues in the in-process
    test transport while preserving the mounted /ui/static contract.
    """

    def __init__(self, directory: Path):
        self.directory = directory.resolve()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            response = Response(status_code=404)
            await response(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if method not in {"GET", "HEAD"}:
            response = Response("Method Not Allowed", status_code=405)
            await response(scope, receive, send)
            return

        root_path = scope.get("root_path", "")
        request_path = scope.get("path", "")
        if root_path and request_path.startswith(root_path):
            request_path = request_path[len(root_path):]

        root = self.directory
        candidate = (root / request_path.lstrip("/")).resolve()
        if root not in candidate.parents or not candidate.is_file():
            response = Response("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        media_type = guess_type(str(candidate))[0] or "application/octet-stream"
        content = b"" if method == "HEAD" else candidate.read_bytes()
        response = Response(
            content,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )
        await response(scope, receive, send)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard_index(request: Request):
    return templates.TemplateResponse(
        request,
        "ui/index.html",
        _template_context(request, active_page="overview"),
    )


@router.get("/status", response_class=HTMLResponse)
async def dashboard_status(request: Request):
    status = await runtime_status()
    return templates.TemplateResponse(
        request,
        "ui/status.html",
        _template_context(request, active_page="status", status=status),
    )


@router.get("/status/panel", response_class=HTMLResponse)
async def dashboard_status_panel(request: Request):
    status = await runtime_status()
    return templates.TemplateResponse(
        request,
        "ui/partials/status_panel.html",
        _template_context(request, status=status),
    )


@router.get("/auth", response_class=HTMLResponse)
async def dashboard_auth(request: Request):
    auth_status = await get_auth_status(refresh=False)
    return templates.TemplateResponse(
        request,
        "ui/auth.html",
        _template_context(request, active_page="auth", auth_status=auth_status),
    )


@router.get("/auth/panel", response_class=HTMLResponse)
async def dashboard_auth_panel(request: Request):
    auth_status = await get_auth_status(refresh=False)
    return templates.TemplateResponse(
        request,
        "ui/partials/auth_panel.html",
        _template_context(request, auth_status=auth_status),
    )


@router.get("/models", response_class=HTMLResponse)
async def dashboard_models(request: Request):
    models = await list_models()
    return templates.TemplateResponse(
        request,
        "ui/models.html",
        _template_context(request, active_page="models", models=models),
    )
