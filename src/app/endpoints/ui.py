from pathlib import Path
from typing import Any
from mimetypes import guess_type
from re import escape as re_escape

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app.endpoints.auth import get_auth_status
from app.endpoints.chat import list_models
from app.endpoints.chat import delete_conversation as delete_conversation_api
from app.endpoints.chat import list_conversations
from app.endpoints.system import runtime_status


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
STATIC_DIR = Path(__file__).resolve().parents[1] / "static" / "ui"

router = APIRouter(prefix="/ui", tags=["Dashboard"], include_in_schema=False)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _template_context(request: Request, **values: Any) -> dict[str, Any]:
    context = {"request": request}
    context.update(values)
    return context


def _mask_conversation_id(conversation_id: Any) -> str:
    if not conversation_id:
        return "n/a"

    value = str(conversation_id)
    if len(value) <= 12:
        return value
    return f"{value[:6]}…{value[-4:]}"


async def _conversation_lookup(conversation_id: str) -> dict[str, Any] | None:
    conversation_list = await list_conversations()
    for conversation in conversation_list.get("data", []):
        if conversation.get("id") == conversation_id:
            row = dict(conversation)
            row["masked_conversation_id"] = _mask_conversation_id(conversation_id)
            row["confirmation_suffix"] = conversation_id[-4:] if len(conversation_id) >= 4 else conversation_id
            row["confirmation_pattern"] = re_escape(row["confirmation_suffix"])
            return row
    return None


def _conversation_delete_error(status_code: int, detail: Any) -> str:
    if status_code == 401:
        return "Gemini authentication is required before this conversation can be deleted."
    if status_code == 404:
        return "This conversation snapshot no longer exists locally."
    if status_code == 409:
        return "This conversation is currently active, busy, or already being deleted."
    if status_code == 503:
        return "The conversation registry or remote Gemini service is unavailable."
    if status_code >= 500:
        return "The delete request failed due to a server-side error."
    return str(detail) if detail else "The delete request could not be completed."


def _conversation_browser_context(
    request: Request,
    conversations: list[dict[str, Any]],
    delete_message: str | None = None,
    delete_error: str | None = None,
    delete_error_status: int | None = None,
) -> dict[str, Any]:
    return _template_context(
        request,
        active_page="conversations",
        conversations=conversations,
        conversation_scope_note="This page shows locally persisted Gemini WebAPI conversation snapshots only.",
        conversation_action_message=delete_message,
        conversation_action_error=delete_error,
        conversation_action_error_status=delete_error_status,
    )


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


@router.get("/playground", response_class=HTMLResponse)
async def dashboard_playground(request: Request):
    models = await list_models()
    return templates.TemplateResponse(
        request,
        "ui/playground.html",
        _template_context(request, active_page="playground", models=models),
    )


@router.get("/conversations", response_class=HTMLResponse)
async def dashboard_conversations(request: Request):
    conversation_list = await list_conversations()
    conversations = conversation_list.get("data", [])
    rows = []
    for conversation in conversations:
        row = dict(conversation)
        row["masked_conversation_id"] = _mask_conversation_id(conversation.get("id"))
        rows.append(row)

    return templates.TemplateResponse(
        request,
        "ui/conversations.html",
        _conversation_browser_context(request, rows),
    )


@router.post("/conversations/delete/confirm", response_class=HTMLResponse)
async def dashboard_conversation_delete_confirm(
    request: Request,
    conversation_id: str = Form(...),
):
    conversation = await _conversation_lookup(conversation_id)
    if conversation is None:
        return templates.TemplateResponse(
            request,
            "ui/partials/conversation_delete_result.html",
            _template_context(
                request,
                delete_error="This conversation snapshot no longer exists locally.",
                delete_error_status=404,
                delete_message=None,
            ),
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "ui/partials/conversation_delete_confirm.html",
        _template_context(
            request,
            conversation=conversation,
        ),
    )


@router.post("/conversations/delete", response_class=HTMLResponse)
async def dashboard_conversation_delete(
    request: Request,
    conversation_id: str = Form(...),
    confirmation_suffix: str = Form(...),
):
    conversation = await _conversation_lookup(conversation_id)
    expected_suffix = conversation_id[-4:] if len(conversation_id) >= 4 else conversation_id

    if confirmation_suffix.strip() != expected_suffix:
        if conversation is None:
            return templates.TemplateResponse(
                request,
                "ui/partials/conversation_delete_result.html",
                _template_context(
                    request,
                    delete_error="This conversation snapshot no longer exists locally.",
                    delete_error_status=404,
                ),
                status_code=404,
            )

        conversation["confirmation_suffix"] = expected_suffix
        return templates.TemplateResponse(
            request,
            "ui/partials/conversation_delete_confirm.html",
            _template_context(
                request,
                conversation=conversation,
                delete_error="Type the last 4 characters of the conversation ID to confirm deletion.",
            ),
            status_code=400,
        )

    try:
        result = await delete_conversation_api(conversation_id)
    except HTTPException as exc:
        error_message = _conversation_delete_error(exc.status_code, exc.detail)
        if conversation is None and exc.status_code == 404:
            return templates.TemplateResponse(
                request,
                "ui/partials/conversation_delete_result.html",
                _template_context(
                    request,
                    delete_error=error_message,
                    delete_error_status=exc.status_code,
                ),
                status_code=exc.status_code,
            )

        if conversation is not None:
            return templates.TemplateResponse(
                request,
                "ui/partials/conversation_delete_confirm.html",
                _template_context(
                    request,
                    conversation=conversation,
                    delete_error=error_message,
                ),
                status_code=exc.status_code,
            )

        return templates.TemplateResponse(
            request,
            "ui/partials/conversation_delete_result.html",
            _template_context(
                request,
                delete_error=error_message,
                delete_error_status=exc.status_code,
            ),
            status_code=exc.status_code,
        )

    response = templates.TemplateResponse(
        request,
        "ui/partials/conversation_delete_result.html",
        _template_context(
            request,
            delete_message=f"Deleted conversation {_mask_conversation_id(result.get('id'))}.",
            delete_error=None,
            delete_error_status=None,
        ),
    )
    response.headers["HX-Redirect"] = "/ui/conversations"
    return response
