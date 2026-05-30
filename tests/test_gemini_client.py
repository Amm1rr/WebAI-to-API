import pytest
import json
import configparser
from unittest.mock import AsyncMock, MagicMock
from app.services.gemini_client import init_gemini_client
import app.services.gemini_client as gemini_client_module

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
        "Cookies": {"__Secure-1PSID": "valid_psid", "__Secure-1PSIDTS": "valid_psidts"}
    })
    mocker.patch('app.services.gemini_client.CONFIG', mock_config)

    # Mock MyGeminiClient
    mock_client_instance = AsyncMock()
    mock_inner_client = MagicMock()
    mock_inner_client.account_status = MagicMock()
    mock_inner_client.account_status.name = "AVAILABLE"
    mock_client_instance.client = mock_inner_client
    
    mock_my_gemini_client_class = mocker.patch(
        'app.services.gemini_client.MyGeminiClient',
        return_value=mock_client_instance
    )

    # Mock get_cookie_from_browser so it's not called
    mock_get_cookies = mocker.patch('app.services.gemini_client.get_cookie_from_browser')

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
    """Verify that a client with UNAUTHENTICATED status is retained as a candidate when config is sole source."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Cookies": {"__Secure-1PSID": "valid_psid"},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.gemini_client.CONFIG', mock_config)

    # Mock MyGeminiClient
    mock_client_instance = AsyncMock()
    mock_inner_client = MagicMock()
    mock_inner_client.account_status = MagicMock()
    mock_inner_client.account_status.name = "UNAUTHENTICATED"
    mock_client_instance.client = mock_inner_client

    mock_my_gemini_client_class = mocker.patch(
        'app.services.gemini_client.MyGeminiClient',
        return_value=mock_client_instance
    )

    # Mock _load_playwright_cookies to return empty
    mock_load_playwright = mocker.patch('app.services.gemini_client._load_playwright_cookies', return_value=(None, None, None))
    # Mock get_cookie_from_browser to return empty
    mock_get_cookies = mocker.patch('app.services.gemini_client.get_cookie_from_browser', return_value=None)

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_client_instance
    assert gemini_client_module._initialization_error is None
    mock_my_gemini_client_class.assert_called_once()
    mock_client_instance.init.assert_called_once_with(verbose=True, auto_refresh=False)
    
    mock_load_playwright.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_location_rejected_discarded_and_fallback(mocker):
    """Verify that a client with LOCATION_REJECTED is discarded and fallback is triggered."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Cookies": {"__Secure-1PSID": "valid_psid"},
        "Browser": {"name": "chrome"},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.gemini_client.CONFIG', mock_config)

    # Mock initial config client (LOCATION_REJECTED)
    mock_config_client = AsyncMock()
    mock_inner_client_rejected = MagicMock()
    mock_inner_client_rejected.account_status = MagicMock()
    mock_inner_client_rejected.account_status.name = "LOCATION_REJECTED"
    mock_config_client.client = mock_inner_client_rejected

    # Mock Playwright fallback client (LOCATION_REJECTED as well, to force browser fallback)
    mock_playwright_client = AsyncMock()
    mock_inner_client_playwright = MagicMock()
    mock_inner_client_playwright.account_status = MagicMock()
    mock_inner_client_playwright.account_status.name = "LOCATION_REJECTED"
    mock_playwright_client.client = mock_inner_client_playwright

    # Mock browser fallback client (AVAILABLE)
    mock_fallback_client = AsyncMock()
    mock_inner_client_available = MagicMock()
    mock_inner_client_available.account_status = MagicMock()
    mock_inner_client_available.account_status.name = "AVAILABLE"
    mock_fallback_client.client = mock_inner_client_available

    # Side effect for MyGeminiClient creation: 1st config, 2nd Playwright, 3rd browser fallback
    mock_my_gemini_client_class = mocker.patch(
        'app.services.gemini_client.MyGeminiClient',
        side_effect=[mock_config_client, mock_playwright_client, mock_fallback_client]
    )

    # Mock _load_playwright_cookies to return cookies (but status evaluates to LOCATION_REJECTED)
    mock_load_playwright = mocker.patch(
        'app.services.gemini_client._load_playwright_cookies',
        return_value=({"__Secure-1PSID": "playwright_psid"}, "playwright_psid", None)
    )

    # Mock get_cookie_from_browser to return valid browser cookies
    mock_get_cookies = mocker.patch(
        'app.services.gemini_client.get_cookie_from_browser',
        return_value={"__Secure-1PSID": "browser_psid"}
    )

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_fallback_client
    assert gemini_client_module._initialization_error is None
    
    # Verify all client instances were handled
    assert mock_my_gemini_client_class.call_count == 3
    mock_config_client.init.assert_called_once()
    mock_config_client.close.assert_called_once()
    
    mock_playwright_client.init.assert_called_once()
    mock_playwright_client.close.assert_called_once()
    
    mock_fallback_client.init.assert_called_once()

    # Verify fallback cookies were requested
    mock_load_playwright.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_playwright_state_fallback(mocker):
    """Verify that when config cookies fail/unauth, the client successfully upgrades to Playwright AVAILABLE cookies."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Cookies": {"__Secure-1PSID": "valid_psid"},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.gemini_client.CONFIG', mock_config)

    # Mock config client (UNAUTHENTICATED)
    mock_config_client = AsyncMock()
    mock_inner_client_rejected = MagicMock()
    mock_inner_client_rejected.account_status = MagicMock()
    mock_inner_client_rejected.account_status.name = "UNAUTHENTICATED"
    mock_config_client.client = mock_inner_client_rejected

    # Mock Playwright fallback client (AVAILABLE)
    mock_playwright_client = AsyncMock()
    mock_inner_client_available = MagicMock()
    mock_inner_client_available.account_status = MagicMock()
    mock_inner_client_available.account_status.name = "AVAILABLE"
    mock_playwright_client.client = mock_inner_client_available

    # Side effect for MyGeminiClient creation: 1st config, 2nd Playwright
    mock_my_gemini_client_class = mocker.patch(
        'app.services.gemini_client.MyGeminiClient',
        side_effect=[mock_config_client, mock_playwright_client]
    )

    # Mock _load_playwright_cookies to return valid cookies
    mock_load_playwright = mocker.patch(
        'app.services.gemini_client._load_playwright_cookies',
        return_value=({"__Secure-1PSID": "playwright_psid"}, "playwright_psid", None)
    )

    # Mock get_cookie_from_browser so it's not called
    mock_get_cookies = mocker.patch('app.services.gemini_client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_playwright_client
    assert gemini_client_module._initialization_error is None

    # Verify calls
    assert mock_my_gemini_client_class.call_count == 2
    mock_config_client.init.assert_called_once()
    mock_config_client.close.assert_called_once()  # Unauth candidate is closed upon upgrade
    mock_playwright_client.init.assert_called_once()
    
    mock_load_playwright.assert_called_once()
    mock_get_cookies.assert_not_called()  # Browser fallback is bypassed


@pytest.mark.asyncio
async def test_init_gemini_client_config_unauth_playwright_unauth(mocker):
    """Verify that when both config and Playwright cookies are UNAUTHENTICATED, config is retained and Playwright is closed."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Cookies": {"__Secure-1PSID": "config_psid"},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.gemini_client.CONFIG', mock_config)

    # Mock config client (UNAUTHENTICATED)
    mock_config_client = AsyncMock()
    mock_inner_config = MagicMock()
    mock_inner_config.account_status = MagicMock()
    mock_inner_config.account_status.name = "UNAUTHENTICATED"
    mock_config_client.client = mock_inner_config

    # Mock Playwright client (UNAUTHENTICATED)
    mock_playwright_client = AsyncMock()
    mock_inner_playwright = MagicMock()
    mock_inner_playwright.account_status = MagicMock()
    mock_inner_playwright.account_status.name = "UNAUTHENTICATED"
    mock_playwright_client.client = mock_inner_playwright

    mock_my_gemini_client_class = mocker.patch(
        'app.services.gemini_client.MyGeminiClient',
        side_effect=[mock_config_client, mock_playwright_client]
    )

    mock_load_playwright = mocker.patch(
        'app.services.gemini_client._load_playwright_cookies',
        return_value=({"__Secure-1PSID": "playwright_psid"}, "playwright_psid", None)
    )

    mock_get_cookies = mocker.patch('app.services.gemini_client.get_cookie_from_browser', return_value=None)

    # Execute
    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client == mock_config_client
    assert gemini_client_module._initialization_error is None

    assert mock_my_gemini_client_class.call_count == 2
    mock_config_client.init.assert_called_once()
    mock_config_client.close.assert_not_called()  # Retained
    
    mock_playwright_client.init.assert_called_once()
    mock_playwright_client.close.assert_called_once()  # Closed as duplicate
    
    mock_load_playwright.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_playwright_available_browser_available(mocker):
    """Verify that when Playwright is AVAILABLE and Browser is AVAILABLE, Playwright is selected and Browser is bypassed."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.gemini_client.CONFIG', mock_config)

    # Mock Playwright client (AVAILABLE)
    mock_playwright_client = AsyncMock()
    mock_inner_playwright = MagicMock()
    mock_inner_playwright.account_status = MagicMock()
    mock_inner_playwright.account_status.name = "AVAILABLE"
    mock_playwright_client.client = mock_inner_playwright

    mock_my_gemini_client_class = mocker.patch(
        'app.services.gemini_client.MyGeminiClient',
        return_value=mock_playwright_client
    )

    mock_load_playwright = mocker.patch(
        'app.services.gemini_client._load_playwright_cookies',
        return_value=({"__Secure-1PSID": "playwright_psid"}, "playwright_psid", None)
    )

    mock_get_cookies = mocker.patch('app.services.gemini_client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client == mock_playwright_client
    assert gemini_client_module._initialization_error is None

    mock_my_gemini_client_class.assert_called_once()
    mock_playwright_client.init.assert_called_once()
    mock_playwright_client.close.assert_not_called()
    
    mock_load_playwright.assert_called_once()
    mock_get_cookies.assert_not_called()  # Bypassed


@pytest.mark.asyncio
async def test_init_gemini_client_all_unavailable(mocker):
    """Verify that when all sources are unavailable, client initialization fails and returns False."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.gemini_client.CONFIG', mock_config)

    mock_my_gemini_client_class = mocker.patch('app.services.gemini_client.MyGeminiClient')
    mock_load_playwright = mocker.patch('app.services.gemini_client._load_playwright_cookies', return_value=(None, None, None))
    mock_get_cookies = mocker.patch('app.services.gemini_client.get_cookie_from_browser', return_value=None)

    # Execute
    res = await init_gemini_client()

    assert res is False
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._initialization_error == "Gemini cookies not found or completely invalid in config, Playwright state, or browser."

    mock_my_gemini_client_class.assert_not_called()
    mock_load_playwright.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


def test_load_playwright_cookies_file_exists(mocker):
    """Verify that _load_playwright_cookies correctly parses the Playwright state file."""
    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.gemini_client.CONFIG', mock_config)

    # Mock os.path.exists
    mocker.patch('os.path.exists', return_value=True)

    # Mock builtins.open to return valid json
    state_content = json.dumps({
        "cookies": [
            {"name": "__Secure-1PSID", "value": "my_psid", "domain": ".google.com"},
            {"name": "__Secure-1PSIDTS", "value": "my_psidts", "domain": ".google.com"},
            {"name": "some_other_cookie", "value": "val", "domain": ".google.com"}
        ]
    })
    mocker.patch('builtins.open', mocker.mock_open(read_data=state_content))

    from app.services.gemini_client import _load_playwright_cookies
    cookies, psid, psidts = _load_playwright_cookies()

    assert cookies == {"__Secure-1PSID": "my_psid", "__Secure-1PSIDTS": "my_psidts"}
    assert psid == "my_psid"
    assert psidts == "my_psidts"
