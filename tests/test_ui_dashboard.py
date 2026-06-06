from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from app.endpoints import ui as ui_module
from app.main import app


async def _get(path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(path)


async def _post(path: str, data: dict[str, str]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.post(path, data=data)


async def _openapi_paths():
    response = await _get("/openapi.json")
    assert response.status_code == 200
    return response.json()["paths"]


def _runtime_status_payload():
    return {
        "engine": {
            "status": "RUNNING",
            "browser_connected": True,
            "browser_generation": 7,
            "is_bootstrap": False,
        },
        "sessions": {
            "gemini": {
                "is_alive": True,
                "metrics": {"active": 1},
                "is_recovering": False,
            }
        },
        "auth": {},
    }


def _auth_status_payload(
    include_source: bool = True,
    webapi_status: str = "AUTHENTICATED",
    playwright_status: str = "VALID_SESSION",
    login_state: str = "IDLE",
    playwright_last_validated: str = "2026-06-02T00:00:00Z",
    validation_details: str = "Cached test validation.",
    legacy_fallback_active: bool = False,
    migration_needed: bool = False,
):
    gemini_webapi = {"status": webapi_status}
    if include_source:
        gemini_webapi["auth_source"] = "[Cookies] legacy config"
    payload = {
        "timestamp": "2026-06-02T00:00:00Z",
        "login_state": login_state,
        "gemini_webapi": gemini_webapi,
        "playwright": {
            "status": playwright_status,
            "auth_state_file": "runtime/auth/gemini.json",
            "last_validated": playwright_last_validated,
            "validation_details": validation_details,
        },
    }
    if legacy_fallback_active:
        payload["playwright"]["legacy_fallback_active"] = True
    if migration_needed:
        payload["playwright"]["migration_needed"] = True
    return payload


def _conversation_list_payload():
    return {
        "object": "list",
        "provider": "gemini",
        "backend": "webapi",
        "count": 2,
        "data": [
            {
                "id": "conv-1234567890abcdef",
                "object": "conversation",
                "provider": "gemini",
                "backend": "webapi",
                "model": "gemini/gemini-3-flash",
                "gem_id": "gem-123",
                "updated_at": "2026-06-02T12:30:00+00:00",
                "schema_version": 1,
                "session_state": {"secret": "opaque"},
            },
            {
                "id": "conv-fedcba0987654321",
                "object": "conversation",
                "provider": "gemini",
                "backend": "webapi",
                "model": "gemini/gemini-3-pro",
                "gem_id": "gem-456",
                "updated_at": "2026-06-02T12:31:00+00:00",
                "schema_version": 1,
                "session_state": {"secret": "opaque"},
            },
        ],
    }


@pytest.mark.asyncio
async def test_ui_index_returns_html():
    response = await _get("/ui")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Runtime Overview" in response.text
    assert "/ui/static/js/htmx.min.js" in response.text


@pytest.mark.asyncio
async def test_ui_status_returns_html_and_uses_htmx_refresh(mocker):
    runtime_status = mocker.patch(
        "app.endpoints.ui.runtime_status",
        return_value=_runtime_status_payload(),
    )

    response = await _get("/ui/status")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Runtime Status" in response.text
    assert "Global Runtime" in response.text
    assert "Playwright Sessions" in response.text
    assert "Browser-backed provider sessions" in response.text
    assert 'hx-get="/ui/status/panel"' in response.text
    assert 'hx-indicator="#status-refresh-indicator"' in response.text
    assert "Refreshing status..." in response.text
    assert "RUNNING" in response.text
    runtime_status.assert_called_once()


@pytest.mark.asyncio
async def test_ui_status_panel_returns_fragment(mocker):
    mocker.patch(
        "app.endpoints.ui.runtime_status",
        return_value=_runtime_status_payload(),
    )

    response = await _get("/ui/status/panel")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Global Runtime" in response.text
    assert "Playwright Sessions" in response.text
    assert "Browser-backed provider sessions" in response.text
    assert "<th>Sessions</th>" not in response.text
    assert "<th>Provider</th>" in response.text
    assert "<th>Alive</th>" in response.text
    assert "<th>Recovering</th>" in response.text
    assert "gemini" in response.text
    assert 'role="status"' in response.text
    assert 'aria-live="polite"' in response.text


@pytest.mark.asyncio
async def test_ui_auth_returns_html_and_uses_htmx_refresh(mocker):
    get_auth_status = mocker.patch(
        "app.endpoints.ui.get_auth_status",
        return_value=_auth_status_payload(),
    )

    response = await _get("/ui/auth")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Authentication Status" in response.text
    assert "<th>Provider</th>" in response.text
    assert "<th>Backend</th>" in response.text
    assert "<th>Status</th>" in response.text
    assert "<th>Auth Source</th>" in response.text
    assert "<th>Last Checked</th>" in response.text
    assert "<th>Indicators</th>" in response.text
    assert 'hx-get="/ui/auth/panel"' in response.text
    assert 'hx-indicator="#auth-refresh-indicator"' in response.text
    assert "Refreshing auth status..." in response.text
    assert "Gemini" in response.text
    assert "WebAPI" in response.text
    assert "Playwright" in response.text
    assert 'class="badge success">AUTHENTICATED<' in response.text or 'class="badge success"' in response.text
    assert "[Cookies] legacy config" in response.text
    assert 'title="Using legacy cookie configuration"' in response.text
    assert 'title="Migrate cookies to the [Gemini] section. Legacy support will be removed in a future release."' in response.text
    assert 'class="indicator-badge indicator-warning"' in response.text
    get_auth_status.assert_called_once_with(refresh=False)


@pytest.mark.asyncio
async def test_ui_auth_panel_returns_fragment(mocker):
    mocker.patch(
        "app.endpoints.ui.get_auth_status",
        return_value=_auth_status_payload(),
    )

    response = await _get("/ui/auth/panel")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Provider Auth" in response.text
    assert "<th>Provider</th>" in response.text
    assert "<th>Backend</th>" in response.text
    assert "<th>Status</th>" in response.text
    assert "<th>Auth Source</th>" in response.text
    assert "<th>Last Checked</th>" in response.text
    assert "<th>Indicators</th>" in response.text
    assert "AUTHENTICATED" in response.text
    assert "[Cookies] legacy config" in response.text
    assert "runtime/auth/gemini.json" in response.text
    assert 'title="Cached test validation."' in response.text
    assert "Info" in response.text
    assert 'class="indicator-badge indicator-neutral"' in response.text
    assert 'title="Using legacy cookie configuration"' in response.text
    assert 'title="Migrate cookies to the [Gemini] section. Legacy support will be removed in a future release."' in response.text
    assert 'class="indicator-badge indicator-warning"' in response.text
    assert 'role="status"' in response.text
    assert 'aria-live="polite"' in response.text


@pytest.mark.asyncio
async def test_ui_auth_panel_renders_n_a_when_webapi_source_missing(mocker):
    mocker.patch(
        "app.endpoints.ui.get_auth_status",
        return_value=_auth_status_payload(include_source=False),
    )

    response = await _get("/ui/auth/panel")

    assert response.status_code == 200
    assert "<code>n/a</code>" in response.text
    assert 'title="n/a"' not in response.text
    assert "n/a" in response.text
    assert 'class="indicator-badge indicator-warning"' not in response.text


@pytest.mark.asyncio
async def test_ui_auth_panel_renders_legacy_and_migration_indicators(mocker):
    mocker.patch(
        "app.endpoints.ui.get_auth_status",
        new=AsyncMock(
            return_value=_auth_status_payload(
                legacy_fallback_active=True,
                migration_needed=True,
            )
        ),
    )

    response = await _get("/ui/auth/panel")

    assert response.status_code == 200
    assert "Info" in response.text
    assert 'title="Cached test validation."' in response.text
    assert "indicator-badge" in response.text
    assert 'class="indicator-badge indicator-warning"' in response.text


def test_ui_auth_normalizer_includes_optional_indicators():
    rows = ui_module._normalize_auth_status(
        _auth_status_payload(legacy_fallback_active=True, migration_needed=True)
    )

    assert rows[0]["provider"] == "Gemini"
    assert rows[0]["backend"] == "WebAPI"
    assert rows[1]["provider"] == "Gemini"
    assert rows[1]["backend"] == "Playwright"
    assert rows[1]["indicators"][0] == {
        "label": "Info",
        "title": "Cached test validation.",
        "severity": "neutral",
    }
    assert rows[0]["indicators"] == [
        {
            "label": "Legacy",
            "title": "Using legacy cookie configuration",
            "severity": "warning",
        },
        {
            "label": "Migration",
            "title": "Migrate cookies to the [Gemini] section. Legacy support will be removed in a future release.",
            "severity": "warning",
        }
    ]
    assert rows[1]["indicators"] == [
        {
            "label": "Info",
            "title": "Cached test validation.",
            "severity": "neutral",
        }
    ]


def test_ui_auth_normalizer_adds_fallback_indicator():
    rows = ui_module._normalize_auth_status(
        {
            "timestamp": "2026-06-02T00:00:00Z",
            "login_state": "IDLE",
            "gemini_webapi": {
                "status": "AUTHENTICATED",
                "auth_source": "browser cookie fallback",
            },
            "playwright": {
                "status": "VALID_SESSION",
                "auth_state_file": "runtime/auth/gemini.json",
                "last_validated": "2026-06-02T00:00:00Z",
                "validation_details": "Cached test validation.",
            },
        }
    )

    assert rows[0]["indicators"] == [
        {
            "label": "Fallback",
            "title": "Using browser cookie fallback authentication",
            "severity": "warning",
        }
    ]


@pytest.mark.asyncio
async def test_ui_auth_panel_renders_status_classes(mocker):
    mocker.patch(
        "app.endpoints.ui.get_auth_status",
        return_value=_auth_status_payload(
            webapi_status="AUTHENTICATED",
            playwright_status="NO_SESSION",
            login_state="LOGIN_IN_PROGRESS",
        ),
    )

    response = await _get("/ui/auth/panel")

    assert response.status_code == 200
    assert 'class="badge success"' in response.text
    assert 'class="badge warning"' in response.text


@pytest.mark.asyncio
async def test_ui_models_returns_html(mocker):
    async def list_models(include_legacy_playwright_aliases=True):
        assert include_legacy_playwright_aliases is False
        return {
            "object": "list",
            "data": [
                {
                    "id": "gemini/gemini-3-flash",
                    "object": "model",
                    "owned_by": "gemini",
                },
                {
                    "id": "playwright/gemini/gemini-3.1-pro",
                    "object": "model",
                    "owned_by": "google",
                },
                {
                    "id": "atlas/MiniMaxAI/MiniMax-M2",
                    "object": "model",
                    "owned_by": "atlascloud",
                },
            ],
        }

    list_models = mocker.patch("app.endpoints.ui.list_models", side_effect=list_models)

    response = await _get("/ui/models")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Available Models" in response.text
    assert "<th>Backend</th>" in response.text
    assert "gemini/gemini-3-flash" in response.text
    assert "playwright/gemini/gemini-3.1-pro" in response.text
    assert "atlas/MiniMaxAI/MiniMax-M2" in response.text
    assert "playwright/gemini-3.1-pro" not in response.text
    assert "WebAPI" in response.text
    assert "Playwright" in response.text
    assert "Atlas" in response.text
    list_models.assert_called_once()
    list_models.assert_called_once_with(include_legacy_playwright_aliases=False)


@pytest.mark.asyncio
async def test_ui_playground_returns_html_and_populates_models(mocker):
    async def list_models(include_legacy_playwright_aliases=True):
        assert include_legacy_playwright_aliases is False
        return {
            "object": "list",
            "data": [
                {
                    "id": "gemini/gemini-3-flash",
                    "object": "model",
                    "owned_by": "gemini",
                },
                {
                    "id": "playwright/gemini/gemini-3.5-flash",
                    "object": "model",
                    "owned_by": "google",
                },
                {
                    "id": "atlas/MiniMaxAI/MiniMax-M2",
                    "object": "model",
                    "owned_by": "atlascloud",
                },
            ],
        }

    list_models = mocker.patch("app.endpoints.ui.list_models", side_effect=list_models)

    response = await _get("/ui/playground")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Chat Completion" in response.text
    assert 'name="model"' in response.text
    assert "gemini/gemini-3-flash" in response.text
    assert "playwright/gemini/gemini-3.5-flash" in response.text
    assert "atlas/MiniMaxAI/MiniMax-M2" in response.text
    assert "playwright/gemini-3.5-flash" not in response.text
    assert "/ui/static/js/playground.js?v=" in response.text
    assert 'fetch("/v1/chat/completions"' not in response.text
    assert "data-file-input" in response.text
    assert "data-file-list" in response.text
    assert "data-clear-files" in response.text
    assert "data-file-guidance" in response.text
    assert "data-file-attachment-summary" in response.text
    assert "No files attached." in response.text
    assert "Gemini WebAPI" in response.text
    assert "Exact text/file interleaving is not preserved by Gemini WebAPI." in response.text
    assert "40 MiB total raw size" in response.text
    list_models.assert_called_once()
    list_models.assert_called_once_with(include_legacy_playwright_aliases=False)


def test_ui_static_mount_uses_staticfiles():
    mounts = [route for route in app.routes if isinstance(route, Mount) and route.path == "/ui/static"]
    assert len(mounts) == 1
    assert isinstance(mounts[0].app, StaticFiles)


def test_ui_static_files_exist_on_disk():
    assert (ui_module.STATIC_DIR / "css/dashboard.css").is_file()
    assert (ui_module.STATIC_DIR / "js/htmx.min.js").is_file()
    assert (ui_module.STATIC_DIR / "js/playground.js").is_file()


@pytest.mark.asyncio
async def test_ui_html_references_static_assets():
    response = await _get("/ui")

    assert response.status_code == 200
    assert "/ui/static/js/htmx.min.js?v=" in response.text
    assert "/ui/static/css/dashboard.css?v=" in response.text

    playground_response = await _get("/ui/playground")
    assert playground_response.status_code == 200
    assert "/ui/static/js/playground.js?v=" in playground_response.text
    playground_js = (ui_module.STATIC_DIR / "js/playground.js").read_text(encoding="utf-8")
    assert "/v1/chat/completions" in playground_js
    assert "AbortController" in playground_js
    assert "setCustomValidity" in playground_js
    assert "reportValidity" in playground_js
    assert "lastConversationId" in playground_js
    assert "lastReusedConversation" in playground_js
    assert "lastReusedConversationSeen" in playground_js
    assert "lastModel" in playground_js
    assert "prompt cannot be empty" in playground_js.lower()
    assert "readAsDataURL" in playground_js
    assert "data-file-input" in playground_js
    assert "data-file-list" in playground_js
    assert "data-clear-files" in playground_js
    assert "data-file-attachment-summary" in playground_js
    assert "Selected files will be attached on submit" in playground_js
    assert "Gemini Playwright and Atlas do not support file parts" in playground_js
    assert "MAX_TOTAL_FILE_SIZE_BYTES = 40 * 1024 * 1024" in playground_js


@pytest.mark.asyncio
async def test_dashboard_docs_mention_playground_file_support():
    docs_path = Path("docs/dashboard.md")
    assert docs_path.is_file()
    docs_text = docs_path.read_text(encoding="utf-8")
    assert "/ui/playground" in docs_text
    assert "optional file attachments for Gemini WebAPI" in docs_text
    assert "Gemini Playwright and Atlas do not support file parts" in docs_text
    assert "conservative file limits" in docs_text


@pytest.mark.asyncio
async def test_ui_conversations_returns_html_and_uses_existing_list_helper(mocker):
    list_conversations = mocker.patch(
        "app.endpoints.ui.list_conversations",
        return_value=_conversation_list_payload(),
    )

    response = await _get("/ui/conversations")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Conversation Snapshots" in response.text
    assert "This page shows locally persisted Gemini WebAPI conversation snapshots only." in response.text
    assert "Playwright and Atlas conversations are not listed here" in response.text
    assert "Delete all local Gemini WebAPI snapshots" in response.text
    assert "2 local Gemini WebAPI snapshots currently available." in response.text
    assert 'hx-post="/ui/conversations/delete/confirm"' in response.text
    assert 'hx-post="/ui/conversations/delete/all/confirm"' in response.text
    assert 'hx-target="#bulk-delete-panel"' in response.text
    assert 'hx-indicator="#conversation-action-indicator"' in response.text
    assert 'Loading conversation action...' in response.text
    assert 'value="conv-1234567890abcdef"' in response.text
    assert "<code>conv-1234567890abcdef</code>" not in response.text
    assert "<code>conv-fedcba0987654321</code>" not in response.text
    assert "cdef" in response.text
    assert "secret" not in response.text
    assert "session_state" not in response.text
    assert "hx-delete" not in response.text
    list_conversations.assert_called_once()


@pytest.mark.asyncio
async def test_ui_conversation_delete_confirm_renders_masked_details(mocker):
    mocker.patch(
        "app.endpoints.ui.list_conversations",
        return_value={
            "object": "list",
            "provider": "gemini",
            "backend": "webapi",
            "count": 1,
            "data": [
                {
                    "id": "conv-1234567890abcdef",
                    "object": "conversation",
                    "provider": "gemini",
                    "backend": "webapi",
                    "model": "gemini/gemini-3-flash",
                    "gem_id": "gem-123",
                    "updated_at": "2026-06-02T12:30:00+00:00",
                    "schema_version": 1,
                    "session_state": {"secret": "opaque"},
                }
            ],
        },
    )

    response = await _post("/ui/conversations/delete/confirm", {"conversation_id": "conv-1234567890abcdef"})

    assert response.status_code == 200
    assert "Confirm Delete" in response.text
    assert 'Deleting conversation...' in response.text
    assert "<code>conv-1234567890abcdef</code>" not in response.text
    assert 'value="conv-1234567890abcdef"' in response.text
    assert "cdef" in response.text
    assert "<label for=\"confirmation_suffix\">" in response.text
    assert 'aria-describedby="confirmation_suffix_help"' in response.text
    assert 'name="confirmation_suffix"' in response.text
    assert 'pattern="cdef"' in response.text
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_ui_conversation_delete_rejects_wrong_confirmation_without_calling_delete(mocker):
    mocker.patch(
        "app.endpoints.ui.list_conversations",
        return_value={
            "object": "list",
            "provider": "gemini",
            "backend": "webapi",
            "count": 1,
            "data": [
                {
                    "id": "conv-1234567890abcdef",
                    "object": "conversation",
                    "provider": "gemini",
                    "backend": "webapi",
                    "model": "gemini/gemini-3-flash",
                    "gem_id": "gem-123",
                    "updated_at": "2026-06-02T12:30:00+00:00",
                    "schema_version": 1,
                    "session_state": {"secret": "opaque"},
                }
            ],
        },
    )
    delete_conversation = mocker.patch("app.endpoints.ui.delete_conversation_api")

    response = await _post(
        "/ui/conversations/delete",
        {
            "conversation_id": "conv-1234567890abcdef",
            "confirmation_suffix": "0000",
        },
    )

    assert response.status_code == 400
    assert "Type the last 4 characters" in response.text
    assert 'role="alert"' in response.text
    delete_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_ui_conversation_delete_success_redirects_and_calls_helper(mocker):
    mocker.patch(
        "app.endpoints.ui.list_conversations",
        return_value={
            "object": "list",
            "provider": "gemini",
            "backend": "webapi",
            "count": 1,
            "data": [
                {
                    "id": "conv-1234567890abcdef",
                    "object": "conversation",
                    "provider": "gemini",
                    "backend": "webapi",
                    "model": "gemini/gemini-3-flash",
                    "gem_id": "gem-123",
                    "updated_at": "2026-06-02T12:30:00+00:00",
                    "schema_version": 1,
                    "session_state": {"secret": "opaque"},
                }
            ],
        },
    )
    delete_conversation = mocker.patch(
        "app.endpoints.ui.delete_conversation_api",
        return_value={
            "id": "conv-1234567890abcdef",
            "object": "conversation.deleted",
            "deleted": True,
            "provider": "gemini",
            "backend": "webapi",
        },
    )

    response = await _post(
        "/ui/conversations/delete",
        {
            "conversation_id": "conv-1234567890abcdef",
            "confirmation_suffix": "cdef",
        },
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/ui/conversations"
    assert 'role="status"' in response.text
    assert "Deleted conversation" in response.text
    delete_conversation.assert_called_once_with("conv-1234567890abcdef")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code, detail, expected",
    [
        (401, "auth required", "authentication is required"),
        (404, "missing", "no longer exists locally"),
        (409, "busy", "currently active, busy, or already being deleted"),
        (503, "timeout", "registry or remote Gemini service is unavailable"),
        (500, "boom", "server-side error"),
    ],
)
async def test_ui_conversation_delete_error_messages(mocker, status_code, detail, expected):
    mocker.patch(
        "app.endpoints.ui.list_conversations",
        return_value={
            "object": "list",
            "provider": "gemini",
            "backend": "webapi",
            "count": 1,
            "data": [
                {
                    "id": "conv-1234567890abcdef",
                    "object": "conversation",
                    "provider": "gemini",
                    "backend": "webapi",
                    "model": "gemini/gemini-3-flash",
                    "gem_id": "gem-123",
                    "updated_at": "2026-06-02T12:30:00+00:00",
                    "schema_version": 1,
                    "session_state": {"secret": "opaque"},
                }
            ],
        },
    )
    mocker.patch(
        "app.endpoints.ui.delete_conversation_api",
        side_effect=HTTPException(status_code=status_code, detail=detail),
    )

    response = await _post(
        "/ui/conversations/delete",
        {
            "conversation_id": "conv-1234567890abcdef",
            "confirmation_suffix": "cdef",
        },
    )

    assert response.status_code == status_code
    assert expected in response.text
    assert 'role="alert"' in response.text


@pytest.mark.asyncio
async def test_ui_bulk_delete_confirm_renders_count_and_scope(mocker):
    mocker.patch("app.endpoints.ui.list_conversations", return_value=_conversation_list_payload())

    response = await _post("/ui/conversations/delete/all/confirm", {})

    assert response.status_code == 200
    assert "Confirm Bulk Delete" in response.text
    assert "Current Snapshots" in response.text
    assert "2" in response.text
    assert "provider" in response.text.lower()
    assert "backend" in response.text.lower()
    assert "Playwright and Atlas conversations are not affected." in response.text
    assert 'name="confirmation_phrase"' in response.text
    assert 'placeholder="DELETE ALL"' in response.text
    assert 'Type DELETE ALL to confirm bulk deletion.' in response.text
    assert 'Delete all snapshots' in response.text
    assert 'id="bulk-delete-panel"' not in response.text
    assert 'Delete all local Gemini WebAPI snapshots' not in response.text


@pytest.mark.asyncio
async def test_ui_bulk_delete_blocks_wrong_phrase_without_calling_helper(mocker):
    mocker.patch("app.endpoints.ui.list_conversations", return_value=_conversation_list_payload())
    delete_conversations = mocker.patch("app.endpoints.ui.delete_conversations_api")

    response = await _post("/ui/conversations/delete/all", {"confirmation_phrase": "delete all"})

    assert response.status_code == 400
    assert "Type DELETE ALL to confirm bulk deletion." in response.text
    assert 'role="alert"' in response.text
    delete_conversations.assert_not_called()


@pytest.mark.asyncio
async def test_ui_bulk_delete_success_renders_counts_and_refreshes_rows(mocker):
    mocker.patch("app.endpoints.ui.list_conversations", return_value=_conversation_list_payload())
    delete_conversations = mocker.patch(
        "app.endpoints.ui.delete_conversations_api",
        return_value={
            "object": "conversation.bulk_delete",
            "provider": "gemini",
            "backend": "webapi",
            "total": 3,
            "deleted_count": 1,
            "failed_count": 1,
            "skipped_active_count": 1,
            "results": [
                {"id": "conv-1234567890abcdef", "status": "deleted", "deleted": True},
                {"id": "conv-active-1111111111", "status": "skipped_active", "deleted": False, "error": "Conversation is currently in use"},
                {
                    "id": "conv-fedcba0987654321",
                    "status": "failed",
                    "deleted": False,
                    "error": "Gemini remote delete failed.",
                },
            ],
        },
    )

    response = await _post("/ui/conversations/delete/all", {"confirmation_phrase": "DELETE ALL"})

    assert response.status_code == 200
    assert 'role="status"' in response.text
    assert "Deleted 1 of 3 snapshots." in response.text
    assert "Skipped 1 active conversations." in response.text
    assert "Failed 1 deletions." in response.text
    assert "This page shows locally persisted Gemini WebAPI conversation snapshots only." in response.text
    assert "deleted" in response.text
    assert "skipped_active" in response.text
    assert "failed" in response.text
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert 'id="conversation-list-panel"' in response.text
    assert "No locally persisted Gemini WebAPI conversation snapshots were returned." not in response.text
    assert "conversation-list-refresh" in response.headers.get("HX-Trigger", "")
    delete_conversations.assert_called_once_with()


def test_dashboard_docs_present():
    docs_path = Path("docs/dashboard.md")
    assert docs_path.exists()
    content = docs_path.read_text(encoding="utf-8")
    assert "administrative interface" in content
    assert "currently have no authentication" in content
    assert "Docker note" in content


@pytest.mark.asyncio
async def test_ui_routes_are_excluded_from_openapi():
    paths = await _openapi_paths()
    assert "/ui" not in paths
    assert "/ui/status" not in paths
    assert "/ui/auth" not in paths
    assert "/ui/models" not in paths
    assert "/ui/playground" not in paths
    assert "/ui/conversations" not in paths
