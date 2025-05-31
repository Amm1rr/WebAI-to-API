"""
Claude AI Client Module.

This module provides the ClaudeClient class for interacting with the Claude AI API.
It handles authentication using cookies, fetching organization ID, creating new chats,
and sending messages (both streaming and non-streaming).

Note: This client appears to use an unofficial API endpoint structure and relies on
      browser cookie impersonation, which might be unstable or against terms of service.
"""
import json
import time
import httpx # For asynchronous HTTP requests, used in streaming
from curl_cffi import requests # For synchronous HTTP requests, mimicking curl with CFFI
import logging

logger = logging.getLogger(__name__)

class ClaudeClient:
    """
    A client for interacting with the Claude AI.

    This client manages communication with Claude's unofficial API,
    handling authentication via cookies, and providing methods to send messages
    and stream responses.

    Attributes:
        cookie (str): The sessionKey cookie string required for authentication.
        organization_id (str): The UUID of the organization associated with the account.
    """
    BASE_URL = "https://claude.ai/api"
    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0'

    def __init__(self, cookie: str):
        """
        Initializes the ClaudeClient.

        Args:
            cookie: The sessionKey cookie string. Can be a raw string or a dict.
                    If a dict, it will be formatted into a string.

        Raises:
            Exception: If the organization ID cannot be fetched.
        """
        self.cookie = self._format_cookie(cookie)
        self.organization_id = self._get_organization_id()
        if not self.organization_id:
            logger.error("Failed to retrieve organization ID. Claude client initialization failed.")
            # Consider raising a more specific custom exception
            raise Exception("Could not initialize ClaudeClient: Organization ID missing.")
        logger.info(f"ClaudeClient initialized with Organization ID: {self.organization_id}")

    def _format_cookie(self, cookie: Union[str, dict]) -> str:
        """
        Formats the cookie into a string if it's provided as a dictionary.

        Args:
            cookie: The cookie, either as a string or a dictionary.

        Returns:
            The cookie formatted as a string.
        """
        if isinstance(cookie, dict):
            # Convert dict to "key1=value1; key2=value2" format
            return "; ".join([f"{key}={value}" for key, value in cookie.items()])
        return cookie

    def _get_organization_id(self) -> Optional[str]:
        """
        Retrieves the organization ID associated with the authenticated account.

        Returns:
            The organization ID string if successful, None otherwise.
        """
        url = f"{self.BASE_URL}/organizations"
        headers = {
            'User-Agent': self.USER_AGENT,
            'Cookie': self.cookie,
            'Accept': 'application/json', # Specify expected response type
        }
        try:
            # Using curl_cffi for this initial synchronous call
            response = requests.get(url, headers=headers, impersonate="chrome110")
            response.raise_for_status() # Raises an exception for 4XX/5XX status codes

            org_data = response.json()
            if org_data and isinstance(org_data, list) and org_data[0] and "id" in org_data[0]:
                return org_data[0]["id"]
            # The new API returns data directly, not nested under "data"
            elif org_data and "data" in org_data and org_data["data"] and "id" in org_data["data"][0]: # old check
                 return org_data["data"][0]["id"]
            else:
                logger.error(f"Organization ID not found in response: {org_data}")
                return None
        except requests.RequestsError as e:
            logger.error(f"HTTP error getting organization ID: {e.response.status_code} - {e.response.text}")
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error(f"Error parsing organization ID response: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred while getting organization ID: {e}")
        return None

    async def send_message(self, prompt: str, model: str) -> str:
        """
        Sends a message to Claude and gets a complete response (non-streaming).

        A new chat conversation is created for each message.

        Args:
            prompt: The message content to send to Claude.
            model: The Claude model to use (e.g., "claude-3-sonnet-20240229").

        Returns:
            The text content of Claude's response.

        Raises:
            Exception: If there's an error sending the message or creating the chat.
        """
        conversation_id = await self._create_new_chat()
        if not conversation_id:
            raise Exception("Failed to create a new chat for sending a message.")

        url = f"{self.BASE_URL}/organizations/{self.organization_id}/chat_conversations/{conversation_id}/completion"
        payload = {
            "prompt": prompt,
            "model": model,
            "timezone": "Europe/London", # Consider making this configurable or deriving it
            "attachments": [], # Support for attachments can be added here
            "files": [], # If files are supported
        }
        headers = {
            'User-Agent': self.USER_AGENT,
            'Cookie': self.cookie,
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream', # Claude API uses SSE for completion
        }

        # For non-streaming, we'd typically not use SSE accept header, but this API might always use it.
        # We'll use httpx for async, even for non-streaming, to keep HTTP client consistent.
        full_response_content = ""
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    response.raise_for_status() # Check for HTTP errors
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                data_chunk = json.loads(line[len("data:"):].strip())
                                if "completion" in data_chunk:
                                    full_response_content += data_chunk["completion"]
                                if data_chunk.get("stop_reason") is not None: # Indicates end of stream
                                    break
                            except json.JSONDecodeError:
                                logger.warning(f"Could not decode JSON from line: {line}")
                                continue # Skip malformed lines
            if not full_response_content: # Check if anything was received
                 logger.warning("No content received from Claude completion endpoint despite 200 OK.")
            return full_response_content
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error sending message to Claude: {e.response.status_code} - {await e.response.aread()}")
            raise Exception(f"Error sending message to Claude: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Unexpected error sending message to Claude: {e}")
            raise Exception(f"Unexpected error sending message to Claude: {str(e)}")


    async def stream_message(self, prompt: str, model: str):
        """
        Sends a message to Claude and streams the response.

        A new chat conversation is created for each message. The streamed response
        is formatted as OpenAI-compatible server-sent events (SSE).

        Args:
            prompt: The message content to send to Claude.
            model: The Claude model to use.

        Yields:
            str: JSON strings representing OpenAI-compatible chat completion chunks.

        Raises:
            Exception: If there's an error creating the chat or during streaming.
        """
        conversation_id = await self._create_new_chat()
        if not conversation_id:
            raise Exception("Failed to create a new chat for streaming.")

        url = f"{self.BASE_URL}/organizations/{self.organization_id}/chat_conversations/{conversation_id}/completion"
        payload = { # Using dict directly for httpx json parameter
            "prompt": prompt,
            "model": model,
            "timezone": "Europe/London", # Consider making this configurable
            "attachments": [],
        }
        headers = {
            'User-Agent': self.USER_AGENT,
            'Cookie': self.cookie,
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream', # Crucial for streaming
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client: # Increased timeout
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    response.raise_for_status() # Raise HTTPStatusError for bad responses (4xx or 5xx)
                    event_id_counter = 0 # Simple counter for event IDs
                    async for line in response.aiter_lines():
                        if not line.strip(): # Skip empty lines often used as keep-alives
                            continue
                        if line.startswith("data:"):
                            content_json_str = line[len("data:"):].strip()
                            try:
                                # The actual content from Claude is within this JSON string
                                data_chunk = json.loads(content_json_str)
                                completion_text = data_chunk.get("completion", "")

                                # Format as OpenAI-compatible chunk
                                yield json.dumps({
                                    "id": f"chatcmpl-{int(time.time())}-{event_id_counter}",
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model,
                                    "choices": [{
                                        "delta": {"content": completion_text},
                                        "index": 0,
                                        "finish_reason": data_chunk.get("stop_reason"), # Pass along if available
                                    }],
                                })
                                event_id_counter += 1
                                if data_chunk.get("stop_reason") is not None:
                                    break # Stop streaming if Claude indicates completion
                            except json.JSONDecodeError:
                                logger.warning(f"Failed to decode JSON from stream: {content_json_str}")
                                # Yield an error chunk or handle as appropriate
                                yield json.dumps({
                                    "id": f"chatcmpl-error-{int(time.time())}",
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model,
                                    "choices": [{"delta": {"content": "[ERROR: Invalid data from upstream]"}, "index": 0, "finish_reason": "error"}]
                                })
                        # Handle other SSE event types if necessary (e.g., event: ping)
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error streaming message from Claude: {e.response.status_code} - {await e.response.aread()}")
            # Yield a final error message in the stream if possible
            yield json.dumps({
                "id": f"chatcmpl-httperror-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"delta": {"content": f"[ERROR: HTTP {e.response.status_code}]"}, "index": 0, "finish_reason": "error"}]
            })
            # Re-raise or handle as appropriate for the application lifecycle
            # raise Exception(f"HTTP error during streaming: {e.response.status_code}") from e
        except Exception as e:
            logger.error(f"Unexpected error streaming message from Claude: {e}")
            yield json.dumps({
                "id": f"chatcmpl-error-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"delta": {"content": f"[ERROR: {str(e)}]"}, "index": 0, "finish_reason": "error"}]
            })
            # raise Exception(f"Unexpected error during streaming: {str(e)}") from e


    async def _create_new_chat(self) -> Optional[str]:
        """
        Creates a new chat conversation.

        Returns:
            The UUID of the newly created chat conversation if successful, None otherwise.
        """
        url = f"{self.BASE_URL}/organizations/{self.organization_id}/chat_conversations"
        # Payload to create a new chat, name can be empty or a title
        payload = {"name": ""} # API might require name, even if empty

        headers = {
            'User-Agent': self.USER_AGENT,
            'Cookie': self.cookie,
            'Content-Type': 'application/json',
            'Accept': 'application/json', # Expecting JSON response
        }

        # Using httpx for async operation
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status() # Good practice to check for HTTP errors

                response_data = response.json()
                if response_data and "uuid" in response_data:
                    logger.info(f"Successfully created new chat with UUID: {response_data['uuid']}")
                    return response_data["uuid"]
                else:
                    logger.error(f"Chat UUID not found in response: {response_data}")
                    return None
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error creating new chat: {e.response.status_code} - {await e.response.aread()}")
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error parsing new chat response: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred while creating new chat: {e}")
        return None