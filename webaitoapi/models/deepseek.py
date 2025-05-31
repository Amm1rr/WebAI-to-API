"""
Deepseek AI Client Module.

This module provides the DeepseekClient class for interacting with the Deepseek AI
platform, presumably via an API endpoint at `chat.deepseek.com`.
It handles authentication using a `user_token` (as a Bearer token) read from
`config.conf` and also attempts to load browser cookies for the `chat.deepseek.com` domain.

Note: The reliance on both a bearer token and browser cookies is somewhat unconventional
      for standard API interactions and might indicate an attempt to interface with
      unofficial or web-session-based endpoints. This could be prone to breakage
      if Deepseek changes its web interface or authentication mechanisms.
"""
import httpx
import configparser
import logging
from fastapi import HTTPException # Used for raising HTTP exceptions directly
from typing import Optional, AsyncGenerator, Dict, Any
import browser_cookie3 # type: ignore

logger = logging.getLogger(__name__)

class DeepseekClient:
    """
    A client for interacting with the Deepseek AI.

    This client uses a user token for Bearer authentication and also attempts to
    load browser cookies for `chat.deepseek.com`. It provides methods to send
    chat messages, primarily designed for streaming responses.

    Attributes:
        user_token (Optional[str]): The user token for Bearer authentication.
        cookies (Dict[str, str]): Cookies loaded from the browser.
        headers (Dict[str, str]): HTTP headers used for requests.
        client (Optional[httpx.AsyncClient]): The httpx client for making async requests.
                                             Initialized in `__aenter__` or `__init__`.
    """
    API_BASE_URL = "https://chat.deepseek.com/api/v1"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initializes the DeepseekClient.

        Args:
            api_key (Optional[str]): An API key (user_token). If not provided, it will
                                     attempt to load `user_token` from `config.conf`.
                                     This parameter name `api_key` is used for consistency
                                     with how `main.py` was trying to pass it, though
                                     the client internally refers to it as `user_token`.
        """
        self.user_token = api_key if api_key else self._load_user_token_from_config()
        self.cookies = self._load_cookies_from_browser()

        self.headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json, text/event-stream", # Accepts JSON and SSE
        }
        if self.user_token:
            self.headers["Authorization"] = f"Bearer {self.user_token}"
        else:
            logger.warning("Deepseek user_token is not available. Client might not authenticate correctly.")

        # Initialize the client directly in __init__ for simplicity if not using __aenter__ exclusively
        # If __aenter__ is preferred for setup, this can be moved/adjusted.
        self.client = httpx.AsyncClient(headers=self.headers, cookies=self.cookies, timeout=httpx.Timeout(30.0))
        logger.info(f"DeepseekClient initialized. User token present: {bool(self.user_token)}, Cookies loaded: {bool(self.cookies)}")

    def _load_user_token_from_config(self) -> Optional[str]:
        """
        Loads the userToken from the `config.conf` file.

        Returns:
            The userToken string if found, otherwise None.
        """
        config = configparser.ConfigParser()
        try:
            # Assuming config.conf is in the application's root directory
            config.read("config.conf")
            user_token = config.get("Deepseek", "user_token", fallback=None)
            if not user_token:
                logger.warning("Deepseek user_token not found in config.conf.")
                return None
            logger.info("Deepseek user_token loaded successfully from config.conf.")
            return user_token
        except Exception as e:
            logger.error(f"Error reading user_token from config.conf: {e}", exc_info=True)
            return None

    def _load_cookies_from_browser(self) -> Dict[str, str]:
        """
        Loads cookies from the configured browser for the `chat.deepseek.com` domain.

        Returns:
            A dictionary of cookie names and values. Returns an empty dict if loading fails.
        """
        config = configparser.ConfigParser()
        try:
            config.read("config.conf")
            browser_name = config.get("Browser", "name", fallback="firefox").lower()

            # Dynamically get the cookie-loading function from browser_cookie3
            if hasattr(browser_cookie3, browser_name):
                cj = getattr(browser_cookie3, browser_name)(domain_name="chat.deepseek.com")
                cookie_dict = {cookie.name: cookie.value for cookie in cj}
                logger.info(f"Successfully loaded {len(cookie_dict)} cookies from {browser_name} for Deepseek.")
                return cookie_dict
            else:
                logger.warning(f"Unsupported browser for cookie loading: {browser_name}")
                return {}
        except Exception as e:
            logger.warning(f"Failed to load cookies for Deepseek: {e}. Proceeding without browser cookies.", exc_info=True)
            return {}

    async def __aenter__(self):
        """
        Asynchronous context manager entry. Ensures the client is ready.
        The client is already initialized in __init__ in this version.
        This method can be used for any additional setup if needed or just return self.
        """
        if not self.client or self.client.is_closed:
            # Re-initialize if closed, though ideally close is only called at the very end.
            self.client = httpx.AsyncClient(headers=self.headers, cookies=self.cookies, timeout=httpx.Timeout(30.0))
            logger.info("httpx.AsyncClient re-initialized in __aenter__.")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any):
        """
        Asynchronous context manager exit. Closes the httpx client.
        """
        await self.close()

    async def chat(self, message: str, model: str = "deepseek-chat", stream: bool = True) -> AsyncGenerator[str, None]:
        """
        Sends a chat message to the Deepseek API and yields response chunks.

        Args:
            message: The user's message content.
            model: The Deepseek model to use (e.g., "deepseek-chat").
            stream: Whether to stream the response. If False, it will accumulate
                    and yield a single string (though the current implementation
                    is primarily for streaming via SSE).

        Yields:
            str: Chunks of the response content as JSON strings (if SSE) or raw text.
                 The format depends on how Deepseek API structures its stream.
                 This implementation assumes it's text chunks from an SSE stream.

        Raises:
            HTTPException: If the API returns an error or the client is not initialized.
        """
        if not self.client or self.client.is_closed:
            logger.error("Deepseek client is not initialized or has been closed.")
            # This should ideally not happen if __aenter__ ensures client readiness.
            raise HTTPException(status_code=500, detail="Deepseek client is not available.")

        url = f"{self.API_BASE_URL}/chat/completions"
        payload = {
            "messages": [{"role": "user", "content": message}],
            "model": model,
            "stream": stream, # Explicitly pass the stream parameter
        }

        logger.info(f"Sending chat to Deepseek: model={model}, stream={stream}, message='{message[:50]}...'")

        try:
            async with self.client.stream("POST", url, json=payload) as response:
                # Log request details (excluding sensitive parts of payload if necessary)
                # logger.debug(f"Deepseek request: POST {url} Payload: {payload}")

                if response.status_code != 200:
                    error_content = await response.aread()
                    logger.error(f"Deepseek API error: {response.status_code} - {error_content.decode()}")
                    raise HTTPException(status_code=response.status_code, detail=f"Failed to send message to Deepseek: {error_content.decode()}")
                
                # Iterate over the text chunks from the stream
                async for chunk_str in response.aiter_text():
                    if chunk_str: # Ensure non-empty chunks
                        # logger.debug(f"Received chunk from Deepseek: {chunk_str[:100]}") # Log first 100 chars
                        yield chunk_str # Yield the raw chunk, assuming SSE data format handled by caller
        except httpx.RequestError as e: # Handles network errors, DNS failures, etc.
            logger.error(f"HTTPX RequestError during Deepseek chat: {e}", exc_info=True)
            raise HTTPException(status_code=503, detail=f"Network error connecting to Deepseek: {str(e)}")
        except Exception as e: # Catch-all for other unexpected errors
            logger.error(f"Unexpected error in Deepseek chat: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"An unexpected error occurred with Deepseek: {str(e)}")

    async def close(self) -> None:
        """
        Closes the httpx AsyncClient.
        Should be called when the DeepseekClient is no longer needed, typically at application shutdown.
        """
        if self.client and not self.client.is_closed:
            try:
                await self.client.aclose()
                logger.info("Deepseek httpx.AsyncClient closed successfully.")
            except Exception as e:
                logger.error(f"Error closing Deepseek httpx.AsyncClient: {e}", exc_info=True)
        # Set client to None or a closed state indicator if desired, though is_closed handles it
        # self.client = None