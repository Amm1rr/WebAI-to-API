from pathlib import Path
from typing import Any
from re import escape as re_escape

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.endpoints.auth import get_auth_status
from app.endpoints.chat import list_models
from app.endpoints.chat import delete_conversation as delete_conversation_api
from app.endpoints.chat import delete_conversations as delete_conversations_api
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
    rows = await _conversation_rows()
    for conversation in rows:
        if conversation.get("id") == conversation_id:
            row = dict(conversation)
            row["masked_conversation_id"] = _mask_conversation_id(conversation_id)
            row["confirmation_suffix"] = conversation_id[-4:] if len(conversation_id) >= 4 else conversation_id
            row["confirmation_pattern"] = re_escape(row["confirmation_suffix"])
            return row
    return None


async def _conversation_rows() -> list[dict[str, Any]]:
    conversation_list = await list_conversations()
    conversations = conversation_list.get("data", [])
    rows = []
    for conversation in conversations:
        row = dict(conversation)
        row["masked_conversation_id"] = _mask_conversation_id(conversation.get("id"))
        rows.append(row)
    return rows


def _bulk_result_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        row = dict(result)
        row["masked_conversation_id"] = _mask_conversation_id(result.get("id"))
        rows.append(row)
    return rows


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
    conversation_count: int | None = None,
    delete_message: str | None = None,
    delete_error: str | None = None,
    delete_error_status: int | None = None,
    bulk_message: str | None = None,
    bulk_error: str | None = None,
    bulk_error_status: int | None = None,
) -> dict[str, Any]:
    return _template_context(
        request,
        active_page="conversations",
        conversations=conversations,
        conversation_count=conversation_count if conversation_count is not None else len(conversations),
        conversation_scope_note="This page shows locally persisted Gemini WebAPI conversation snapshots only.",
        conversation_action_message=delete_message,
        conversation_action_error=delete_error,
        conversation_action_error_status=delete_error_status,
        bulk_message=bulk_message,
        bulk_error=bulk_error,
        bulk_error_status=bulk_error_status,
    )


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
    rows = await _conversation_rows()

    return templates.TemplateResponse(
        request,
        "ui/conversations.html",
        _conversation_browser_context(request, rows),
    )


@router.post("/conversations/delete/all/confirm", response_class=HTMLResponse)
async def dashboard_conversation_bulk_delete_confirm(request: Request):
    rows = await _conversation_rows()
    return templates.TemplateResponse(
        request,
        "ui/partials/conversation_bulk_delete_confirm.html",
        _template_context(
            request,
            bulk_count=len(rows),
            bulk_provider="gemini",
            bulk_backend="webapi",
            bulk_warning=(
                "This action may partially succeed.\n"
                "Active conversations can be skipped.\n"
                "Remote delete failures may occur."
            ),
            bulk_scope_note="Playwright and Atlas conversations are not affected.",
        ),
    )


@router.post("/conversations/delete/all", response_class=HTMLResponse)
async def dashboard_conversation_bulk_delete(request: Request, confirmation_phrase: str = Form(...)):
    expected_phrase = "DELETE ALL"
    if confirmation_phrase != expected_phrase:
        rows = await _conversation_rows()
        return templates.TemplateResponse(
            request,
            "ui/partials/conversation_bulk_delete_confirm.html",
            _template_context(
                request,
                bulk_count=len(rows),
                bulk_provider="gemini",
                bulk_backend="webapi",
                bulk_warning=(
                    "This action may partially succeed.\n"
                    "Active conversations can be skipped.\n"
                    "Remote delete failures may occur."
                ),
                bulk_scope_note="Playwright and Atlas conversations are not affected.",
                bulk_error="Type DELETE ALL to confirm bulk deletion.",
            ),
            status_code=400,
        )

    try:
        result = await delete_conversations_api()
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "ui/partials/conversation_bulk_delete_result.html",
            _template_context(
                request,
                bulk_error=_conversation_delete_error(exc.status_code, exc.detail),
                bulk_error_status=exc.status_code,
            ),
            status_code=exc.status_code,
        )

    rows = await _conversation_rows()
    response = templates.TemplateResponse(
        request,
        "ui/partials/conversation_bulk_delete_result.html",
        _template_context(
            request,
            bulk_result=result,
            bulk_result_rows=_bulk_result_rows(result.get("results", [])),
            bulk_message=(
                f"Deleted {result.get('deleted_count', 0)} of {result.get('total', 0)} snapshots. "
                f"Skipped {result.get('skipped_active_count', 0)} active conversations. "
                f"Failed {result.get('failed_count', 0)} deletions."
            ),
            bulk_conversation_count=len(rows),
        ),
    )
    response.headers["HX-Trigger"] = "conversation-list-refresh"
    return response


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
