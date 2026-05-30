import pytest
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
    """Verify that a client with UNAUTHENTICATED status is retained and registered."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Cookies": {"__Secure-1PSID": "valid_psid"}
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

    # Mock get_cookie_from_browser
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
        "Browser": {"name": "chrome"}
    })
    mocker.patch('app.services.gemini_client.CONFIG', mock_config)

    # Mock initial config client (LOCATION_REJECTED)
    mock_config_client = AsyncMock()
    mock_inner_client_rejected = MagicMock()
    mock_inner_client_rejected.account_status = MagicMock()
    mock_inner_client_rejected.account_status.name = "LOCATION_REJECTED"
    mock_config_client.client = mock_inner_client_rejected

    # Mock fallback browser client (AVAILABLE)
    mock_fallback_client = AsyncMock()
    mock_inner_client_available = MagicMock()
    mock_inner_client_available.account_status = MagicMock()
    mock_inner_client_available.account_status.name = "AVAILABLE"
    mock_fallback_client.client = mock_inner_client_available

    # Side effect for MyGeminiClient creation: 1st call for config, 2nd call for browser fallback
    mock_my_gemini_client_class = mocker.patch(
        'app.services.gemini_client.MyGeminiClient',
        side_effect=[mock_config_client, mock_fallback_client]
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
    
    # Verify both client instances were initialized
    assert mock_my_gemini_client_class.call_count == 2
    mock_config_client.init.assert_called_once()
    mock_config_client.close.assert_called_once()  # Rejected client must be closed
    mock_fallback_client.init.assert_called_once()

    # Verify fallback cookies were requested
    mock_get_cookies.assert_called_once_with("gemini")
