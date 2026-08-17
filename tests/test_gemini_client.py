import pytest
import json
import asyncio
import configparser
from unittest.mock import AsyncMock, MagicMock
from app.services.providers.gemini.client import close_gemini_client, init_gemini_client
import app.services.providers.gemini.client as gemini_client_module
from app.services.browser.auth_loader import GeminiAuthStateLoader
from app.services.providers.gemini.auth_selector import GeminiAuthCandidate, GeminiAuthSelector


@pytest.fixture(autouse=True)
def reset_gemini_client_state():
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None
    gemini_client_module._gemini_client_auth_source = None
    gemini_client_module._retired_gemini_clients.clear()
    yield
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None
    gemini_client_module._gemini_client_auth_source = None
    gemini_client_module._retired_gemini_clients.clear()


class Status:
    def __init__(self, name):
        self.name = name


def make_mock_client(status_name):
    client = AsyncMock()
    client.client = MagicMock(account_status=Status(status_name))
    return client


def auth_data(psid):
    return {
        "cookies": [
            {"name": "__Secure-1PSID", "value": psid, "domain": ".google.com"}
        ]
    }


def auth_candidate(source_name, source_type, psid, is_legacy=False):
    return GeminiAuthCandidate(
        source_name=source_name,
        source_type=source_type,
        auth_data=auth_data(psid),
        is_legacy=is_legacy,
        supports_webapi_cookie_auth=True,
        supports_playwright_storage=True,
        migration_needed=is_legacy,
    )


async def _block_until_release(entered, release):
    entered.set()
    await release.wait()


def patch_auth_sources(mocker, gemini=None, legacy=None, json_source=None):
    return (
        mocker.patch.object(
            GeminiAuthStateLoader,
            'get_gemini_config_source',
            return_value=(gemini, False),
        ),
        mocker.patch.object(
            GeminiAuthStateLoader,
            'get_legacy_cookie_source',
            return_value=(legacy, legacy is not None),
        ),
        mocker.patch.object(
            GeminiAuthStateLoader,
            'get_json_source',
            return_value=(json_source, False),
        ),
    )


@pytest.mark.asyncio
async def test_disabled_gemini_clears_previous_auth_source(mocker):
    old_client = make_mock_client("AVAILABLE")
    gemini_client_module._gemini_client = old_client
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "false"}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)

    assert await init_gemini_client() is False
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._gemini_client_auth_source is None
    assert gemini_client_module._initialization_error == "Gemini client is disabled in config."
    old_client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_failed_init_clears_previous_auth_source(mocker):
    old_client = make_mock_client("AVAILABLE")
    gemini_client_module._gemini_client = old_client
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
    })
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(GeminiAuthStateLoader, "get_gemini_config_source", return_value=(None, False))
    mocker.patch.object(GeminiAuthStateLoader, "get_legacy_cookie_source", return_value=(None, False))
    mocker.patch.object(GeminiAuthStateLoader, "get_json_source", return_value=(None, False))
    mocker.patch("app.services.providers.gemini.client.get_cookie_from_browser", return_value=None)

    assert await init_gemini_client() is False
    assert gemini_client_module._gemini_client is old_client
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    assert gemini_client_module._initialization_error is None


@pytest.mark.asyncio
async def test_replacement_keeps_old_client_published_during_candidate_init(mocker):
    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    entered = asyncio.Event()
    release = asyncio.Event()
    async def block_init(**kwargs):
        await _block_until_release(entered, release)

    new_client.init.side_effect = block_init
    gemini_client_module._gemini_client = old_client
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    task = asyncio.create_task(init_gemini_client())
    await entered.wait()
    assert gemini_client_module._gemini_client is old_client
    assert gemini_client_module.get_gemini_client_auth_source() == "[Gemini] config"
    release.set()
    assert await task is True


@pytest.mark.asyncio
async def test_failed_candidate_closes_candidate_and_preserves_old_client(mocker):
    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    new_client.init.side_effect = RuntimeError("candidate failed")
    gemini_client_module._gemini_client = old_client
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    assert await init_gemini_client() is False
    assert gemini_client_module._gemini_client is old_client
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    new_client.close.assert_awaited_once_with()
    assert new_client not in gemini_client_module._retired_gemini_clients


@pytest.mark.asyncio
async def test_successful_replacement_updates_registry_and_retains_old_client(mocker):
    from app.services.providers.gemini.session_manager import SessionRegistry

    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    registry = SessionRegistry(old_client)
    gemini_client_module._gemini_client = old_client
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    assert await init_gemini_client(registry_updater=lambda: registry.update_client(new_client)) is True
    assert gemini_client_module._gemini_client is new_client
    assert gemini_client_module.get_gemini_client_auth_source() == "replacement"
    assert registry.client is new_client
    assert registry.client_generation == 1
    old_client.close.assert_not_awaited()
    assert old_client in gemini_client_module._retired_gemini_clients


@pytest.mark.asyncio
async def test_registry_update_failure_rolls_back_replacement(mocker):
    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    gemini_client_module._gemini_client = old_client
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    async def fail_registry_update():
        raise RuntimeError("registry failed")

    assert await init_gemini_client(registry_updater=fail_registry_update) is False
    assert gemini_client_module._gemini_client is old_client
    assert gemini_client_module.get_gemini_client_auth_source() == "[Gemini] config"
    new_client.close.assert_awaited_once_with()
    old_client.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_gemini_client_closes_and_resets_state(mocker):
    client = make_mock_client("AVAILABLE")
    gemini_client_module._gemini_client = client
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"
    gemini_client_module._initialization_error = "old error"

    await close_gemini_client()

    client.close.assert_awaited_once_with()
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._gemini_client_auth_source is None
    assert gemini_client_module._initialization_error is None


@pytest.mark.asyncio
async def test_close_gemini_client_closes_retired_clients_and_clears_list():
    current = make_mock_client("AVAILABLE")
    retired = [make_mock_client("AVAILABLE"), make_mock_client("AVAILABLE")]
    gemini_client_module._gemini_client = current
    gemini_client_module._retired_gemini_clients.extend(retired)

    await close_gemini_client()

    current.close.assert_awaited_once_with()
    for client in retired:
        client.close.assert_awaited_once_with()
    assert gemini_client_module._retired_gemini_clients == []


@pytest.mark.asyncio
async def test_close_gemini_client_deduplicates_clients_by_identity():
    client = make_mock_client("AVAILABLE")
    gemini_client_module._gemini_client = client
    gemini_client_module._retired_gemini_clients.extend([client, client])

    await close_gemini_client()

    client.close.assert_awaited_once_with()
    assert gemini_client_module._retired_gemini_clients == []


@pytest.mark.asyncio
async def test_close_gemini_client_continues_after_retired_close_failure():
    current = make_mock_client("AVAILABLE")
    failed = make_mock_client("AVAILABLE")
    remaining = make_mock_client("AVAILABLE")
    failed.close.side_effect = RuntimeError("close failed")
    gemini_client_module._gemini_client = current
    gemini_client_module._retired_gemini_clients.extend([failed, remaining])

    await close_gemini_client()

    current.close.assert_awaited_once_with()
    failed.close.assert_awaited_once_with()
    remaining.close.assert_awaited_once_with()
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._retired_gemini_clients == []


@pytest.mark.asyncio
async def test_close_gemini_client_is_safe_without_client_and_on_repeat():
    await close_gemini_client()
    await close_gemini_client()


@pytest.mark.asyncio
async def test_close_gemini_client_resets_state_when_close_fails(mocker):
    client = make_mock_client("AVAILABLE")
    client.close.side_effect = RuntimeError("close failed")
    gemini_client_module._gemini_client = client
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"
    gemini_client_module._initialization_error = "old error"

    await close_gemini_client()

    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._gemini_client_auth_source is None
    assert gemini_client_module._initialization_error is None


@pytest.mark.asyncio
async def test_init_gemini_client_available(mocker):
    """Verify that a client with AVAILABLE status is successfully retained and registered."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG using a real ConfigParser populated via read_dict
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Gemini": {"__Secure-1PSID": "valid_psid", "__Secure-1PSIDTS": "valid_psidts"},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    # Mock MyGeminiClient
    mock_client_instance = make_mock_client("AVAILABLE")
    
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_client_instance
    )

    # Mock get_cookie_from_browser so it's not called
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_client_instance
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    mock_my_gemini_client_class.assert_called_once()
    mock_client_instance.init.assert_called_once_with(verbose=True, auto_refresh=False)
    mock_get_cookies.assert_not_called()


@pytest.mark.asyncio
async def test_init_gemini_client_unauthenticated_retained(mocker):
    """Verify that a client with UNAUTHENTICATED status is retained as a candidate when loader returns it."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock MyGeminiClient
    mock_client_instance = make_mock_client("UNAUTHENTICATED")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_client_instance
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser to return empty
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser', return_value=None)

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_client_instance
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    mock_my_gemini_client_class.assert_called_once()
    mock_client_instance.init.assert_called_once_with(verbose=True, auto_refresh=False)
    
    mock_gemini_source.assert_called_once()
    mock_legacy_source.assert_called_once()
    mock_json_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_location_rejected_discarded_and_fallback(mocker):
    """Verify that a client with LOCATION_REJECTED is discarded and browser fallback is triggered."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock initial loader client (LOCATION_REJECTED)
    mock_loader_client = make_mock_client("LOCATION_REJECTED")

    # Mock browser fallback client (AVAILABLE)
    mock_fallback_client = make_mock_client("AVAILABLE")

    # Side effect for MyGeminiClient creation: 1st loader, 2nd browser fallback
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_loader_client, mock_fallback_client]
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser to return valid browser cookies
    mock_get_cookies = mocker.patch(
        'app.services.providers.gemini.client.get_cookie_from_browser',
        return_value={"__Secure-1PSID": "browser_psid"}
    )

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_fallback_client
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "browser cookie fallback"
    
    # Verify all client instances were handled
    assert mock_my_gemini_client_class.call_count == 2
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_called_once()
    
    mock_fallback_client.init.assert_called_once()

    # Verify fallback cookies were requested
    mock_gemini_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_playwright_state_fallback(mocker):
    """Verify that when loader cookies are UNAUTHENTICATED, client successfully upgrades to browser AVAILABLE cookies."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock loader client (UNAUTHENTICATED)
    mock_loader_client = make_mock_client("UNAUTHENTICATED")

    # Mock browser fallback client (AVAILABLE)
    mock_fallback_client = make_mock_client("AVAILABLE")

    # Side effect for MyGeminiClient creation: 1st loader, 2nd browser fallback
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_loader_client, mock_fallback_client]
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser to return valid browser cookies
    mock_get_cookies = mocker.patch(
        'app.services.providers.gemini.client.get_cookie_from_browser',
        return_value={"__Secure-1PSID": "browser_psid"}
    )

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_fallback_client
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "browser cookie fallback"

    # Verify calls
    assert mock_my_gemini_client_class.call_count == 2
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_called_once()  # Closed upon upgrade
    mock_fallback_client.init.assert_called_once()
    
    mock_gemini_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_config_unauth_playwright_unauth(mocker):
    """Verify that when both loader and browser cookies are UNAUTHENTICATED, loader candidate is retained and browser client is closed."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock loader client (UNAUTHENTICATED)
    mock_loader_client = make_mock_client("UNAUTHENTICATED")

    # Mock browser client (UNAUTHENTICATED)
    mock_browser_client = make_mock_client("UNAUTHENTICATED")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_loader_client, mock_browser_client]
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser to return valid browser cookies
    mock_get_cookies = mocker.patch(
        'app.services.providers.gemini.client.get_cookie_from_browser',
        return_value={"__Secure-1PSID": "browser_psid"}
    )

    # Execute
    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client == mock_loader_client
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"

    assert mock_my_gemini_client_class.call_count == 2
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_not_called()  # Retained
    
    mock_browser_client.init.assert_called_once()
    mock_browser_client.close.assert_called_once()  # Closed as duplicate
    
    mock_gemini_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_playwright_available_browser_available(mocker):
    """Verify that when Loader is AVAILABLE and Browser is AVAILABLE, Loader is selected and Browser is bypassed."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock loader client (AVAILABLE)
    mock_loader_client = make_mock_client("AVAILABLE")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_loader_client
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser so it's not called
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client == mock_loader_client
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"

    mock_my_gemini_client_class.assert_called_once()
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_not_called()
    
    mock_gemini_source.assert_called_once()
    mock_get_cookies.assert_not_called()  # Bypassed


@pytest.mark.asyncio
async def test_init_gemini_client_all_unavailable(mocker):
    """Verify that when all sources are unavailable, client initialization fails and returns False."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    mock_my_gemini_client_class = mocker.patch('app.services.providers.gemini.client.MyGeminiClient')
    
    # Mock GeminiAuthStateLoader to return None (no cookies available)
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(mocker)
    
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser', return_value=None)

    # Execute
    res = await init_gemini_client()

    assert res is False
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._initialization_error == "Gemini cookies not found or completely invalid in canonical store, legacy config, or browser."
    assert gemini_client_module._gemini_client_auth_source is None

    mock_my_gemini_client_class.assert_not_called()
    mock_gemini_source.assert_called_once()
    mock_legacy_source.assert_called_once()
    mock_json_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


# =============================================================================
# Source Iteration Tests (Config Source Priority Chain)
# =============================================================================

@pytest.mark.asyncio
async def test_init_gemini_client_consumes_selector_candidates_in_order(mocker):
    """Verify WebAPI initialization consumes GeminiAuthSelector candidates in order."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    candidates = [
        auth_candidate("[Gemini] config", "gemini_config", "gemini_psid"),
        auth_candidate("[Cookies] legacy config", "legacy_cookies", "cookies_psid", is_legacy=True),
    ]
    selector = mocker.patch(
        'app.services.providers.gemini.client.GeminiAuthSelector.iter_candidates',
        return_value=iter(candidates),
    )

    mock_gemini_client = make_mock_client("UNAUTHENTICATED")
    mock_cookies_client = make_mock_client("AVAILABLE")
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_gemini_client, mock_cookies_client]
    )
    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client is mock_cookies_client
    assert gemini_client_module._gemini_client_auth_source == "[Cookies] legacy config"
    selector.assert_called_once()
    assert [call.kwargs["secure_1psid"] for call in mock_my_gemini_client_class.call_args_list] == [
        "gemini_psid",
        "cookies_psid",
    ]


@pytest.mark.asyncio
async def test_init_gemini_client_source_iteration_unauth_to_avail(mocker):
    """Verify that when [Gemini] is UNAUTHENTICATED, [Cookies] is tried and AVAILABLE is selected."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    # Track which sources were attempted
    attempted_sources = []

    def mock_gemini_source():
        attempted_sources.append("gemini")
        return {"cookies": [{"name": "__Secure-1PSID", "value": "gemini_psid", "domain": ".google.com"}]}, False

    def mock_cookies_source():
        attempted_sources.append("cookies")
        return {"cookies": [{"name": "__Secure-1PSID", "value": "cookies_psid", "domain": ".google.com"}]}, True

    def mock_json_source():
        attempted_sources.append("json")
        return None, False

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', side_effect=mock_gemini_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', side_effect=mock_cookies_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_json_source', side_effect=mock_json_source)

    # Mock clients: [Gemini] -> UNAUTHENTICATED, [Cookies] -> AVAILABLE
    mock_gemini_client = make_mock_client("UNAUTHENTICATED")

    mock_cookies_client = make_mock_client("AVAILABLE")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_gemini_client, mock_cookies_client]
    )

    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    # Assertions - externally observable behavior only
    assert res is True  # Function succeeded
    assert gemini_client_module._gemini_client is mock_cookies_client  # AVAILABLE client selected
    assert gemini_client_module._gemini_client_auth_source == "[Cookies] legacy config"
    assert attempted_sources == ["gemini", "cookies"]  # Both tried in correct order


@pytest.mark.asyncio
async def test_init_gemini_client_source_chain_multiple_unauth_to_avail(mocker):
    """Verify that all config sources are tried when earlier ones are UNAUTHENTICATED."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    attempted_sources = []

    def mock_gemini_source():
        attempted_sources.append("gemini")
        return auth_data("gemini_psid"), False

    def mock_cookies_source():
        attempted_sources.append("cookies")
        return auth_data("cookies_psid"), True

    def mock_json_source():
        attempted_sources.append("json")
        return auth_data("json_psid"), False

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', side_effect=mock_gemini_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', side_effect=mock_cookies_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_json_source', side_effect=mock_json_source)

    mock_gemini_client = make_mock_client("UNAUTHENTICATED")

    mock_cookies_client = make_mock_client("UNAUTHENTICATED")

    mock_json_client = make_mock_client("AVAILABLE")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_gemini_client, mock_cookies_client, mock_json_client]
    )

    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    # Assertions - externally observable behavior only
    assert res is True  # Function succeeded
    assert gemini_client_module._gemini_client is mock_json_client  # AVAILABLE client selected
    assert gemini_client_module._gemini_client_auth_source == "gemini.json canonical store"
    assert attempted_sources == ["gemini", "cookies", "json"]  # All tried in correct order


@pytest.mark.asyncio
async def test_init_gemini_client_available_short_circuit(mocker):
    """Verify that when [Gemini] is AVAILABLE, no lower-priority sources are attempted."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    attempted_sources = []

    def mock_gemini_source():
        attempted_sources.append("gemini")
        return auth_data("gemini_psid"), False

    def mock_cookies_source():
        attempted_sources.append("cookies")
        return auth_data("cookies_psid"), True

    def mock_json_source():
        attempted_sources.append("json")
        return auth_data("json_psid"), False

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', side_effect=mock_gemini_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', side_effect=mock_cookies_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_json_source', side_effect=mock_json_source)

    mock_gemini_client = make_mock_client("AVAILABLE")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_gemini_client
    )

    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    # Assertions - externally observable behavior only
    assert res is True  # Function succeeded
    assert gemini_client_module._gemini_client is mock_gemini_client  # First client selected
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    assert len(attempted_sources) == 1  # Only first source tried
    assert attempted_sources == ["gemini"]


@pytest.mark.asyncio
async def test_init_gemini_client_guest_mode_fallback_preserved(mocker):
    """Verify that when all sources are UNAUTHENTICATED, guest-mode fallback is retained."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    attempted_sources = []

    def mock_gemini_source():
        attempted_sources.append("gemini")
        return auth_data("gemini_psid"), False

    def mock_cookies_source():
        attempted_sources.append("cookies")
        return auth_data("cookies_psid"), True

    def mock_json_source():
        attempted_sources.append("json")
        return auth_data("json_psid"), False

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', side_effect=mock_gemini_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', side_effect=mock_cookies_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_json_source', side_effect=mock_json_source)

    # All clients UNAUTHENTICATED
    mock_gemini_client = make_mock_client("UNAUTHENTICATED")

    mock_cookies_client = make_mock_client("UNAUTHENTICATED")

    mock_json_client = make_mock_client("UNAUTHENTICATED")

    mock_browser_client = make_mock_client("UNAUTHENTICATED")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_gemini_client, mock_cookies_client, mock_json_client, mock_browser_client]
    )

    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser',
                return_value={"__Secure-1PSID": "browser_psid"})

    # Execute
    res = await init_gemini_client()

    # Assertions - verify guest-mode client is retained and initialization succeeds
    assert res is True  # Function succeeded with guest-mode fallback
    assert gemini_client_module._gemini_client is mock_gemini_client  # Highest-priority guest fallback retained
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    assert attempted_sources == ["gemini", "cookies", "json"]  # All config sources tried


@pytest.mark.asyncio
async def test_init_gemini_client_json_store_source_label(mocker):
    """Verify canonical gemini.json initialization records the canonical source label."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None
    gemini_client_module._gemini_client_auth_source = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    mock_json_client = make_mock_client("AVAILABLE")
    mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_json_client
    )

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', return_value=(None, False))
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', return_value=(None, False))
    mocker.patch.object(
        GeminiAuthStateLoader,
        'get_json_source',
        return_value=({"cookies": [{"name": "__Secure-1PSID", "value": "json_psid", "domain": ".google.com"}]}, False),
    )
    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client is mock_json_client
    assert gemini_client_module._gemini_client_auth_source == "gemini.json canonical store"


@pytest.mark.asyncio
async def test_refresh_status_preserves_webapi_source_when_authenticated(mocker):
    """Verify refresh_status reports the current WebAPI source without clearing it."""
    from app.services.providers.gemini.auth import GeminiAuthStrategy

    gemini_client_module._gemini_client = make_mock_client("AVAILABLE")
    gemini_client_module._gemini_client_auth_source = "[Cookies] legacy config"

    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([auth_candidate("[Cookies] legacy config", "legacy_cookies", "cookies_psid", is_legacy=True)]),
    )

    status = GeminiAuthStrategy().refresh_status()

    assert status["webapi"] == "AUTHENTICATED"
    assert status["webapi_source"] == "[Cookies] legacy config"
    assert gemini_client_module._gemini_client_auth_source == "[Cookies] legacy config"
