import httpx
import configparser
import logging
from fastapi import HTTPException
from typing import Optional, AsyncGenerator

# Configure logging
logger = logging.getLogger(__name__)

class DeepseekClient:
    def __init__(self):
        self.user_token = self.load_user_token()  # Load userToken as a session token
        self.cookies = self.load_cookies()  # Load cookies from the browser
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Authorization": f"Bearer {self.user_token}",  # Use userToken as a Bearer token
        }
        self.client = httpx.AsyncClient(headers=self.headers, cookies=self.cookies)  # Initialize client here

    def load_user_token(self) -> str:
        """Load the userToken from the configuration file."""
        config = configparser.ConfigParser()
        config.read("config.conf")
        user_token = config.get("Deepseek", "user_token", fallback=None)
        if not user_token:
            raise HTTPException(status_code=500, detail="Deepseek userToken not found in config.")
        return user_token

    def load_cookies(self) -> dict:
        """Load cookies from the browser for chat.deepseek.com."""
        try:
            import browser_cookie3
            cookies = browser_cookie3.brave(domain_name="chat.deepseek.com")
            cookie_dict = {cookie.name: cookie.value for cookie in cookies}
            return cookie_dict
        except Exception as e:
            logger.warning(f"Failed to load cookies: {e}. Proceeding without cookies.")
            return {}

    async def __aenter__(self):
        """Initialize the httpx client with headers and cookies."""
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Close the httpx client."""
        await self.close()

    async def chat(self, message: str, model: str = "deepseek-chat") -> AsyncGenerator[str, None]:
        """Send a chat message to Deepseek API."""
        if not self.client:
            raise HTTPException(status_code=500, detail="Deepseek client is not initialized.")

        url = "https://chat.deepseek.com/api/v1/chat/completions"
        payload = {
            "messages": [{"role": "user", "content": message}],
            "model": model,
            "stream": True,
        }

        try:
            async with self.client.stream("POST", url, json=payload) as response:
                if response.status_code != 200:
                    raise HTTPException(status_code=response.status_code, detail="Failed to send message to Deepseek.")
                
                async for chunk in response.aiter_text():
                    yield chunk
        except Exception as e:
            logger.error(f"Error in Deepseek chat: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def close(self):
        """Close the client."""
        if self.client:
            await self.client.aclose()
            self.client = None