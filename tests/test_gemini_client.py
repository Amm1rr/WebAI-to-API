import pytest
import json
import configparser
from unittest.mock import AsyncMock, MagicMock
from app.services.providers.gemini.client import init_gemini_client
import app.services.providers.gemini.client as gemini_client_module
from app.services.browser.auth_loader import GeminiAuthStateLoader

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
        "Cookies": {"__Secure-1PSID": "valid_psid", "__Secure-1PSIDTS": "valid_psidts"},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    # Mock MyGeminiClient
    mock_client_instance = AsyncMock()
    mock_inner_client = MagicMock()
    mock_inner_client.account_status = MagicMock()
    mock_inner_client.account_status.name = "AVAILABLE"
    mock_client_instance.client = mock_inner_client
    
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
    mock_client_instance = AsyncMock()
    mock_inner_client = MagicMock()
    mock_inner_client.account_status = MagicMock()
    mock_inner_client.account_status.name = "UNAUTHENTICATED"
    mock_client_instance.client = mock_inner_client

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_client_instance
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    valid_auth_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "some_psid", "domain": ".google.com"}
        ]
    }
    mock_load_fallback = mocker.patch(
        'app.services.browser.auth_loader.GeminiAuthStateLoader.load_auth_state_with_fallback',
        return_value=(valid_auth_data, False)
    )

    # Mock get_cookie_from_browser to return empty
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser', return_value=None)

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_client_instance
    assert gemini_client_module._initialization_error is None
    mock_my_gemini_client_class.assert_called_once()
    mock_client_instance.init.assert_called_once_with(verbose=True, auto_refresh=False)
    
    mock_load_fallback.assert_called_once()
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
    mock_loader_client = AsyncMock()
    mock_inner_client_rejected = MagicMock()
    mock_inner_client_rejected.account_status = MagicMock()
    mock_inner_client_rejected.account_status.name = "LOCATION_REJECTED"
    mock_loader_client.client = mock_inner_client_rejected

    # Mock browser fallback client (AVAILABLE)
    mock_fallback_client = AsyncMock()
    mock_inner_client_available = MagicMock()
    mock_inner_client_available.account_status = MagicMock()
    mock_inner_client_available.account_status.name = "AVAILABLE"
    mock_fallback_client.client = mock_inner_client_available

    # Side effect for MyGeminiClient creation: 1st loader, 2nd browser fallback
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_loader_client, mock_fallback_client]
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    valid_auth_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "some_psid", "domain": ".google.com"}
        ]
    }
    mock_load_fallback = mocker.patch(
        'app.services.browser.auth_loader.GeminiAuthStateLoader.load_auth_state_with_fallback',
        return_value=(valid_auth_data, False)
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
    
    # Verify all client instances were handled
    assert mock_my_gemini_client_class.call_count == 2
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_called_once()
    
    mock_fallback_client.init.assert_called_once()

    # Verify fallback cookies were requested
    mock_load_fallback.assert_called_once()
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
    mock_loader_client = AsyncMock()
    mock_inner_client_rejected = MagicMock()
    mock_inner_client_rejected.account_status = MagicMock()
    mock_inner_client_rejected.account_status.name = "UNAUTHENTICATED"
    mock_loader_client.client = mock_inner_client_rejected

    # Mock browser fallback client (AVAILABLE)
    mock_fallback_client = AsyncMock()
    mock_inner_client_available = MagicMock()
    mock_inner_client_available.account_status = MagicMock()
    mock_inner_client_available.account_status.name = "AVAILABLE"
    mock_fallback_client.client = mock_inner_client_available

    # Side effect for MyGeminiClient creation: 1st loader, 2nd browser fallback
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_loader_client, mock_fallback_client]
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    valid_auth_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "some_psid", "domain": ".google.com"}
        ]
    }
    mock_load_fallback = mocker.patch(
        'app.services.browser.auth_loader.GeminiAuthStateLoader.load_auth_state_with_fallback',
        return_value=(valid_auth_data, False)
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

    # Verify calls
    assert mock_my_gemini_client_class.call_count == 2
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_called_once()  # Closed upon upgrade
    mock_fallback_client.init.assert_called_once()
    
    mock_load_fallback.assert_called_once()
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
    mock_loader_client = AsyncMock()
    mock_inner_loader = MagicMock()
    mock_inner_loader.account_status = MagicMock()
    mock_inner_loader.account_status.name = "UNAUTHENTICATED"
    mock_loader_client.client = mock_inner_loader

    # Mock browser client (UNAUTHENTICATED)
    mock_browser_client = AsyncMock()
    mock_inner_browser = MagicMock()
    mock_inner_browser.account_status = MagicMock()
    mock_inner_browser.account_status.name = "UNAUTHENTICATED"
    mock_browser_client.client = mock_inner_browser

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_loader_client, mock_browser_client]
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    valid_auth_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "some_psid", "domain": ".google.com"}
        ]
    }
    mock_load_fallback = mocker.patch(
        'app.services.browser.auth_loader.GeminiAuthStateLoader.load_auth_state_with_fallback',
        return_value=(valid_auth_data, False)
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

    assert mock_my_gemini_client_class.call_count == 2
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_not_called()  # Retained
    
    mock_browser_client.init.assert_called_once()
    mock_browser_client.close.assert_called_once()  # Closed as duplicate
    
    mock_load_fallback.assert_called_once()
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
    mock_loader_client = AsyncMock()
    mock_inner_loader = MagicMock()
    mock_inner_loader.account_status = MagicMock()
    mock_inner_loader.account_status.name = "AVAILABLE"
    mock_loader_client.client = mock_inner_loader

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_loader_client
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    valid_auth_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "some_psid", "domain": ".google.com"}
        ]
    }
    mock_load_fallback = mocker.patch(
        'app.services.browser.auth_loader.GeminiAuthStateLoader.load_auth_state_with_fallback',
        return_value=(valid_auth_data, False)
    )

    # Mock get_cookie_from_browser so it's not called
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client == mock_loader_client
    assert gemini_client_module._initialization_error is None

    mock_my_gemini_client_class.assert_called_once()
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_not_called()
    
    mock_load_fallback.assert_called_once()
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
    mock_load_fallback = mocker.patch(
        'app.services.browser.auth_loader.GeminiAuthStateLoader.load_auth_state_with_fallback',
        return_value=(None, False)
    )
    
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser', return_value=None)

    # Execute
    res = await init_gemini_client()

    assert res is False
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._initialization_error == "Gemini cookies not found or completely invalid in canonical store, legacy config, or browser."

    mock_my_gemini_client_class.assert_not_called()
    mock_load_fallback.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")
