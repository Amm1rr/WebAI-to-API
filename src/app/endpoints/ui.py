from pathlib import Path
from typing import Any
from re import escape as re_escape

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.endpoints.auth import get_auth_status
from app.services.model_catalog import list_models
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


def _status_class(status: Any) -> str:
    value = str(status or "n/a").upper()
    if value in {"AUTHENTICATED", "VALID_SESSION"}:
        return "success"
    if value in {"GUEST", "NO_SESSION", "LOGIN_IN_PROGRESS"}:
        return "warning"
    if value in {"EXPIRED_SESSION", "INVALID", "BLOCKED", "LOCATION_REJECTED"}:
        return "danger"
    return "neutral"


def _format_note_value(value: Any) -> str:
    if value is None:
        return "n/a"
    text = str(value).strip()
    return text if text else "n/a"


def _normalize_auth_status(auth_status: dict[str, Any]) -> list[dict[str, Any]]:
    login_state = _format_note_value(auth_status.get("login_state"))
    timestamp = _format_note_value(auth_status.get("timestamp"))
    gemini_webapi = auth_status.get("gemini_webapi") or {}
    playwright = auth_status.get("playwright") or {}

    webapi_status = _format_note_value(gemini_webapi.get("status"))
    webapi_source = _format_note_value(gemini_webapi.get("auth_source"))
    webapi_notes = login_state if login_state != "IDLE" else "n/a"
    webapi_indicators: list[dict[str, str]] = []
    if webapi_source == "[Cookies] legacy config":
        webapi_indicators.append(
            {
                "label": "Legacy",
                "title": "Using legacy cookie configuration",
                "severity": "warning",
            }
        )
        webapi_indicators.append(
            {
                "label": "Migration",
                "title": "Migrate cookies to the [Gemini] section. Legacy support will be removed in a future release.",
                "severity": "warning",
            }
        )
    elif webapi_source == "browser cookie fallback":
        webapi_indicators.append(
            {
                "label": "Fallback",
                "title": "Using browser cookie fallback authentication",
                "severity": "warning",
            }
        )

    playwright_status = _format_note_value(playwright.get("status"))
    playwright_source = _format_note_value(playwright.get("auth_state_file"))
    playwright_validated = _format_note_value(playwright.get("last_validated"))
    playwright_indicators: list[dict[str, str]] = []
    validation_details = _format_note_value(playwright.get("validation_details"))
    if validation_details != "n/a":
        playwright_indicators.append({"label": "Info", "title": validation_details, "severity": "neutral"})

    return [
        {
            "provider": "Gemini",
            "backend": "WebAPI",
            "status": webapi_status,
            "auth_source": webapi_source,
            "last_checked": timestamp,
            "notes": webapi_notes,
            "indicators": webapi_indicators,
            "status_class": _status_class(webapi_status),
        },
        {
            "provider": "Gemini",
            "backend": "Playwright",
            "status": playwright_status,
            "auth_source": playwright_source,
            "last_checked": playwright_validated,
            "indicators": playwright_indicators,
            "status_class": _status_class(playwright_status),
        },
    ]


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


def _model_backend(model: dict[str, Any]) -> str:
    model_id = str(model.get("id", "") or "")

    if model_id.startswith("playwright/"):
        return "Playwright"
    if model_id.startswith("atlas/"):
        return "Atlas"
    return "WebAPI"


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
        _template_context(
            request,
            active_page="auth",
            auth_status=auth_status,
            auth_rows=_normalize_auth_status(auth_status),
        ),
    )


@router.get("/auth/panel", response_class=HTMLResponse)
async def dashboard_auth_panel(request: Request):
    auth_status = await get_auth_status(refresh=False)
    return templates.TemplateResponse(
        request,
        "ui/partials/auth_panel.html",
        _template_context(
            request,
            auth_status=auth_status,
            auth_rows=_normalize_auth_status(auth_status),
        ),
    )


@router.get("/models", response_class=HTMLResponse)
async def dashboard_models(request: Request):
    models = await list_models(include_legacy_playwright_aliases=False)
    model_rows = [
        {
            **model,
            "backend": _model_backend(model),
        }
        for model in models.get("data", [])
    ]
    return templates.TemplateResponse(
        request,
        "ui/models.html",
        _template_context(request, active_page="models", models={**models, "data": model_rows}),
    )


@router.get("/playground", response_class=HTMLResponse)
async def dashboard_playground(request: Request):
    models = await list_models(include_legacy_playwright_aliases=False)
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
            bulk_conversations=rows,
            bulk_scope_note="This page shows locally persisted Gemini WebAPI conversation snapshots only.",
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
