"""
Gemini AI Client Module.

This module provides MyGeminiClient, a wrapper around the `gemini_webapi.GeminiClient`
for interacting with Google's Gemini models via its web API (unofficial).
It simplifies the initialization and usage of the underlying client.
"""
import logging
from typing import Optional, List
from gemini_webapi import GeminiClient as WebGeminiClient # The actual client being wrapped
from gemini_webapi.types import GenerateContentResponse # For type hinting response

logger = logging.getLogger(__name__)

class MyGeminiClient:
    """
    A wrapper client for Google's Gemini API using the `gemini_webapi` library.

    This class simplifies interaction with the Gemini web API by managing the
    client lifecycle and providing straightforward methods for content generation.
    It requires authentication cookies (__Secure-1PSID and __Secure-1PSIDTS).

    Attributes:
        client (WebGeminiClient): The underlying Gemini Web API client instance.
    """

    def __init__(self, secure_1psid: str, secure_1psidts: str):
        """
        Initializes MyGeminiClient.

        Args:
            secure_1psid: The __Secure-1PSID cookie value for authentication.
            secure_1psidts: The __Secure-1PSIDTS cookie value for authentication.
        """
        if not secure_1psid or not secure_1psidts:
            logger.error("Missing one or both Gemini authentication cookies (__Secure-1PSID, __Secure-1PSIDTS).")
            # Consider raising an error if cookies are missing, as the client will be non-functional.
            # raise ValueError("Gemini authentication cookies are required.")
        self.client = WebGeminiClient(secure_1psid, secure_1psidts)
        logger.info("MyGeminiClient initialized with WebGeminiClient.")

    async def init(self) -> None:
        """
        Initializes the underlying Gemini Web API client.

        This typically involves making an initial request to prepare the client
        for subsequent API calls. It should be called before any other operations.
        """
        try:
            await self.client.init()
            logger.info("Underlying WebGeminiClient initialized successfully.")
        except Exception as e:
            logger.error(f"Error during WebGeminiClient initialization: {e}", exc_info=True)
            # Depending on severity, might re-raise or handle to allow graceful degradation
            raise # Re-raise the exception to signal failure in initialization

    async def generate_content(self, message: str, model: str, images: Optional[List[str]] = None) -> GenerateContentResponse:
        """
        Generates content using the specified Gemini model.

        Args:
            message: The textual prompt or message to send to the model.
            model: The specific Gemini model to use (e.g., "gemini-1.5-pro", "gemini-1.5-flash").
                   Note: The `gemini_webapi` library might have its own way of specifying models,
                   ensure this 'model' string is compatible or mapped correctly if needed.
            images: An optional list of base64 encoded image strings to include with the prompt.

        Returns:
            A GenerateContentResponse object from the `gemini_webapi` library,
            containing the model's response.

        Raises:
            Exception: Can propagate exceptions from the underlying `self.client.generate_content` call.
        """
        logger.info(f"Generating content with Gemini model: {model}, images present: {bool(images)}")
        try:
            if images:
                # Call the underlying client's method with images
                response = await self.client.generate_content(prompt=message, model=model, images=images)
            else:
                # Call without images
                response = await self.client.generate_content(prompt=message, model=model)
            logger.info(f"Successfully received content from Gemini model: {model}")
            return response
        except Exception as e:
            logger.error(f"Error generating content with Gemini: {e}", exc_info=True)
            # Re-raise the exception to be handled by the calling endpoint
            raise

    async def close(self) -> None:
        """
        Closes the underlying Gemini Web API client and its session.

        This should be called during application shutdown to release resources.
        """
        try:
            await self.client.close()
            logger.info("Underlying WebGeminiClient closed successfully.")
        except Exception as e:
            logger.error(f"Error during WebGeminiClient close: {e}", exc_info=True)
            # Decide if this error needs to be propagated
            # For a close operation, often logging is sufficient.