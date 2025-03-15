from typing import Optional, List
from gemini_webapi import GeminiClient as WebGeminiClient

class MyGeminiClient:
    """
    Wrapper for the Gemini Web API client.
    """

    def __init__(self, secure_1psid: str, secure_1psidts: str) -> None:
        self.client = WebGeminiClient(secure_1psid, secure_1psidts)

    async def init(self) -> None:
        """Initialize the Gemini client."""
        await self.client.init()

    async def generate_content(self, message: str, model: str, images: Optional[List[str]] = None):
        """
        Generate content using the Gemini client.

        :param message: The input message.
        :param model: The model to use.
        :param images: Optional list of image URLs.
        :return: The response from the Gemini API.
        """
        return await self.client.generate_content(message, model=model, images=images)

    async def close(self) -> None:
        """Close the Gemini client."""
        await self.client.close()

    def start_chat(self, model: str):
        """
        Start a chat session with the given model.

        :param model: The model to use for the chat session.
        :return: A chat session instance.
        """
        return self.client.start_chat(model=model)
