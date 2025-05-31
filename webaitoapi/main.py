"""
Main FastAPI application for WebAI-to-API.

This module sets up and runs a FastAPI server that provides endpoints to interact with
various AI models like Claude, Gemini, and Deepseek. It handles request routing,
AI client initialization, and response formatting.
"""
import os
import time
import json
import configparser
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Union, Dict, Any
from contextlib import asynccontextmanager
import logging
import browser_cookie3 # type: ignore
from enum import Enum

# Import AI client models
from models.claude import ClaudeClient
from models.gemini import MyGeminiClient
from models.deepseek import DeepseekClient

# --- Model Definitions ---
class ClaudeModels(str, Enum):
    """Enum for available Claude models."""
    SONNET = "claude-3-sonnet-20240229"
    SONNET_5 = "claude-3-5-sonnet-20241022"
    HAIKU_5 = "claude-3-5-haiku-20241022"

class GeminiModels(str, Enum):
    """Enum for available Gemini models."""
    FLASH = "gemini-1.5-flash"
    FLASH_EXP = "gemini-2.0-flash-exp"
    PRO = "gemini-1.5-pro"

class DeepseekModels(str, Enum):
    """Enum for available Deepseek models."""
    CHAT = "deepseek-chat"
    REASONER = "deepseek-reasoner"

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration Loading ---
config = configparser.ConfigParser()
# Ensure config.conf exists or handle its absence
if not os.path.exists("config.conf"):
    logger.warning("config.conf not found. Creating a default one. Please review and update it.")
    # Create a default config structure if it doesn't exist
    config["AI"] = {"default_ai": "gemini",
                    "default_model_gemini": GeminiModels.PRO.value,
                    "default_model_claude": ClaudeModels.SONNET_5.value}
    config["Cookies"] = {"claude_cookie": "",
                         "gemini_cookie_1PSID": "",
                         "gemini_cookie_1PSIDTS": ""}
    config["Deepseek"] = {"user_token": ""}
    config["EnabledAI"] = {"claude": "false",
                           "gemini": "true",
                           "deepseek": "false"}
    config["Browser"] = {"name": "firefox"} # Common default
    with open("config.conf", "w") as configfile:
        config.write(configfile)
else:
    config.read("config.conf")


# Set default browser if not specified in config
if "Browser" not in config or "name" not in config["Browser"]:
    logger.info("Browser not specified in config, defaulting to 'firefox'.")
    if "Browser" not in config:
        config["Browser"] = {}
    config["Browser"]["name"] = "firefox"
    with open("config.conf", "w") as configfile: # Save update
        config.write(configfile)

# Ensure Cookies section exists
if "Cookies" not in config:
    logger.info("Cookies section not found in config, creating empty section.")
    config["Cookies"] = {}
    with open("config.conf", "w") as configfile: # Save update
        config.write(configfile)

# --- Enabled AI Services ---
# Read which AIs are enabled, defaulting to False if not specified
ENABLED_AI = {
    "claude": config.getboolean("EnabledAI", "claude", fallback=False),
    "gemini": config.getboolean("EnabledAI", "gemini", fallback=True), # Default to True for Gemini
    "deepseek": config.getboolean("EnabledAI", "deepseek", fallback=False),
}
logger.info(f"Enabled AI services: {ENABLED_AI}")

# --- Pydantic Request Schemas ---
class Message(BaseModel):
    """Represents a single message in a chat conversation."""
    role: Literal["user", "assistant"] = Field(..., description="The role of the message sender.")
    content: str = Field(..., description="The content of the message.")

class ClaudeRequest(BaseModel):
    """Request schema for the /claude endpoint."""
    message: str = Field(..., description="The user's message to Claude.")
    model: ClaudeModels = Field(default=ClaudeModels.SONNET_5, description="The Claude model to use.")
    stream: Optional[bool] = Field(default=False, description="Whether to stream the response.")

class GeminiRequest(BaseModel):
    """Request schema for the /gemini endpoint."""
    message: str = Field(..., description="The user's message to Gemini.")
    model: GeminiModels = Field(default=GeminiModels.PRO, description="The Gemini model to use.")
    images: Optional[List[str]] = Field(default_factory=list, description="Optional list of base64 encoded image strings.")

class DeepseekRequest(BaseModel):
    """Request schema for the /deepseek endpoint."""
    message: str = Field(..., description="The user's message to Deepseek.")
    model: DeepseekModels = Field(default=DeepseekModels.CHAT, description="The Deepseek model to use.")
    stream: Optional[bool] = Field(default=False, description="Whether to stream the response.")

class OpenAIChatRequest(BaseModel):
    """
    Request schema for the /v1/chat/completions endpoint, mimicking OpenAI's format.
    """
    messages: List[Message] = Field(..., description="A list of messages forming the conversation history.")
    model: Optional[Union[ClaudeModels, GeminiModels, DeepseekModels]] = Field(
        default=None,
        description="The model to use. If None, uses default_ai from config.conf."
    )
    stream: Optional[bool] = Field(default=False, description="Whether to stream the response.")
    # Add other common OpenAI parameters if needed in the future, e.g., temperature, max_tokens

# --- Global AI Client Instances ---
# These will be initialized during the application lifespan
claude_client: Optional[ClaudeClient] = None
gemini_client: Optional[MyGeminiClient] = None
deepseek_client: Optional[DeepseekClient] = None

# --- Application Lifespan Management (Startup/Shutdown) ---
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """
    Asynchronous context manager to handle application startup and shutdown events.
    Initializes AI clients on startup and closes them on shutdown.
    """
    global claude_client, gemini_client, deepseek_client
    logger.info("Application startup: Initializing AI clients...")

    # Initialize Claude client if enabled
    if ENABLED_AI["claude"]:
        try:
            claude_cookie = config["Cookies"].get("claude_cookie")
            if not claude_cookie: # Try to get from browser if not in config
                logger.info("Claude cookie not in config, trying to fetch from browser.")
                claude_cookie_tuple = get_cookie_from_browser("claude")
                if claude_cookie_tuple: # get_cookie_from_browser for claude returns a single string or None
                    claude_cookie = str(claude_cookie_tuple)

            if claude_cookie:
                claude_client = ClaudeClient(claude_cookie)
                logger.info("Claude client initialized successfully.")
            else:
                logger.warning("Claude cookie not found in config or browser. Claude API will not be available.")
                ENABLED_AI["claude"] = False # Disable if cookie is essential and not found
        except Exception as e:
            logger.error(f"Failed to initialize Claude client: {e}", exc_info=True)
            claude_client = None
            ENABLED_AI["claude"] = False # Disable on error
    else:
        logger.info("Claude client is disabled in config. Skipping initialization.")

    # Initialize Gemini client if enabled
    if ENABLED_AI["gemini"]:
        try:
            gemini_cookie_1PSID = config["Cookies"].get("gemini_cookie_1PSID")
            gemini_cookie_1PSIDTS = config["Cookies"].get("gemini_cookie_1PSIDTS")

            if not gemini_cookie_1PSID or not gemini_cookie_1PSIDTS: # Try to get from browser
                logger.info("Gemini cookies not in config, trying to fetch from browser.")
                gemini_cookies_tuple = get_cookie_from_browser("gemini")
                if gemini_cookies_tuple and len(gemini_cookies_tuple) == 2:
                    gemini_cookie_1PSID, gemini_cookie_1PSIDTS = gemini_cookies_tuple

            if gemini_cookie_1PSID and gemini_cookie_1PSIDTS:
                gemini_client = MyGeminiClient(gemini_cookie_1PSID, gemini_cookie_1PSIDTS)
                await gemini_client.init() # Initialize any async resources for Gemini
                logger.info("Gemini client initialized successfully.")
            else:
                logger.warning("Gemini cookies (__Secure-1PSID, __Secure-1PSIDTS) not found in config or browser. Gemini API will not be available.")
                ENABLED_AI["gemini"] = False # Disable if cookies are essential
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}", exc_info=True)
            gemini_client = None
            ENABLED_AI["gemini"] = False # Disable on error
    else:
        logger.info("Gemini client is disabled in config. Skipping initialization.")

    # Initialize Deepseek client if enabled
    if ENABLED_AI["deepseek"]:
        try:
            # Assuming DeepseekClient might use a token from config
            deepseek_token = config["Deepseek"].get("user_token")
            if not deepseek_token:
                logger.warning("Deepseek user_token not found in config. Deepseek client may not function correctly if it relies on a token.")
            # Pass token if constructor expects it, or handle within DeepseekClient
            deepseek_client = DeepseekClient(api_key=deepseek_token if deepseek_token else None)
            logger.info("Deepseek client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Deepseek client: {e}", exc_info=True)
            deepseek_client = None
            ENABLED_AI["deepseek"] = False # Disable on error
    else:
        logger.info("Deepseek client is disabled in config. Skipping initialization.")

    yield # Application runs here

    # Cleanup actions on shutdown
    logger.info("Application shutdown: Closing AI clients...")
    if gemini_client:
        try:
            await gemini_client.close()
            logger.info("Gemini client closed successfully.")
        except Exception as e:
            logger.error(f"Error closing Gemini client: {e}", exc_info=True)
    if deepseek_client:
        try:
            await deepseek_client.close() # Assuming it has an async close method
            logger.info("Deepseek client closed successfully.")
        except Exception as e:
            logger.error(f"Error closing Deepseek client: {e}", exc_info=True)
    if claude_client:
        # Claude client from anthropic library doesn't have an explicit close method
        logger.info("Claude client does not require explicit closing.")


# --- Helper Functions ---
def get_cookie_from_browser(service: Literal["claude", "gemini"]) -> Optional[Union[str, tuple[str, str]]]:
    """
    Retrieves authentication cookies for a given service from the specified browser.

    Args:
        service: The AI service for which to retrieve cookies ("claude" or "gemini").

    Returns:
        For "claude", returns the sessionKey cookie value as a string, or None if not found.
        For "gemini", returns a tuple of (__Secure-1PSID, __Secure-1PSIDTS) cookie values,
        or None if not found.
        Returns None if an error occurs or cookies are not found.
    """
    browser_name = config["Browser"].get("name", "firefox").lower()
    logger.info(f"Attempting to get cookies from browser: {browser_name} for service: {service}")
    cj = None
    try:
        # Dynamically call the browser_cookie3 function for the specified browser
        if hasattr(browser_cookie3, browser_name):
            cj = getattr(browser_cookie3, browser_name)(domain_name="google.com" if service == "gemini" else "claude.ai")
            logger.info(f"Successfully retrieved cookies from {browser_name} for domain related to {service}.")
        else:
            raise ValueError(f"Unsupported browser configured: {browser_name}")
    except Exception as e:
        logger.error(f"Failed to retrieve cookies from {browser_name} for {service}: {e}", exc_info=True)
        return None

    if not cj:
        logger.warning(f"No cookies found for {service} using browser {browser_name}.")
        return None

    if service == "claude":
        logger.info("Looking for Claude cookie (sessionKey)...")
        for cookie in cj:
            if cookie.name == "sessionKey" and "claude.ai" in cookie.domain: # More specific domain
                logger.info(f"Found Claude cookie (sessionKey) for claude.ai.")
                return cookie.value
        logger.warning("Claude cookie (sessionKey) not found for claude.ai.")
        return None
    elif service == "gemini":
        logger.info("Looking for Gemini cookies (__Secure-1PSID and __Secure-1PSIDTS for google.com)...")
        secure_1psid = None
        secure_1psidts = None
        # Filter cookies for *.google.com or specific relevant domains
        for cookie in cj:
            if "google.com" in cookie.domain: # Check for relevant domain
                if cookie.name == "__Secure-1PSID":
                    secure_1psid = cookie.value
                    logger.info(f"Found __Secure-1PSID for {cookie.domain}: {secure_1psid[:15]}...") # Log partial value for privacy
                elif cookie.name == "__Secure-1PSIDTS":
                    secure_1psidts = cookie.value
                    logger.info(f"Found __Secure-1PSIDTS for {cookie.domain}: {secure_1psidts[:15]}...") # Log partial value

        if secure_1psid and secure_1psidts:
            logger.info("Both Gemini cookies (__Secure-1PSID, __Secure-1PSIDTS) found for google.com.")
            return secure_1psid, secure_1psidts
        else:
            logger.warning("One or both Gemini cookies not found for google.com.")
            return None
    else:
        logger.warning(f"Unsupported service specified for cookie retrieval: {service}")
        return None

def convert_to_openai_format(response_content: str, model_name: str, stream: bool = False) -> Dict[str, Any]:
    """
    Converts a raw text response or a simple content string into a dictionary
    structured like an OpenAI ChatCompletion or ChatCompletionChunk object.

    This function is primarily used as a fallback for formatting responses from AI clients
    that do not natively produce OpenAI-compatible chunk structures during streaming,
    or for formatting complete non-streaming responses.

    Args:
        response_content: The textual content of the AI's response or a delta content.
        model_name: The name of the model that generated the response.
        stream: If True, formats as a `chat.completion.chunk` with a `delta` field.
                If False, formats as a `chat.completion` with a `message` field and
                includes a placeholder `usage` field.
        response_id_val: Optional. If provided, uses this as the ID. Otherwise generates one.
                         Useful for ensuring consistent IDs across a stream if handled by caller.
        finish_reason_val: Optional. Sets the `finish_reason`. Important for the last chunk of a stream.


    Returns:
        A dictionary formatted like an OpenAI ChatCompletion or ChatCompletionChunk object.
    """
    timestamp = int(time.time())
    response_id = response_id_val if response_id_val else f"chatcmpl-{'s' if stream else 'ns'}-{timestamp}-{hash(response_content if response_content else '')%100000}"

    choice_item = {
        "index": 0,
        "finish_reason": None, # Default, can be overridden by finish_reason_val
    }

    if stream:
        choice_item["delta"] = {"role": "assistant", "content": response_content}
        if finish_reason_val: # Set finish_reason if provided for a stream chunk (typically the last one)
            choice_item["finish_reason"] = finish_reason_val
    else: # Non-streaming
        choice_item["message"] = {"role": "assistant", "content": response_content}
        choice_item["finish_reason"] = finish_reason_val if finish_reason_val else "stop"

    # The 'usage' field is only included for non-streaming responses.
    # Token counts are not currently implemented/retrieved from the underlying APIs.
    usage_stats = None
    if not stream:
        usage_stats = {
            "prompt_tokens": 0, # Placeholder - token counts not implemented
            "completion_tokens": 0, # Placeholder - token counts not implemented
            "total_tokens": 0, # Placeholder - token counts not implemented
        }

    response_dict = {
        "id": response_id,
        "object": "chat.completion.chunk" if stream else "chat.completion",
        "created": timestamp,
        "model": model_name,
        "choices": [choice_item],
    }

    if usage_stats: # Only add 'usage' if it's a non-streaming response
        response_dict["usage"] = usage_stats

    return response_dict

# --- FastAPI Application Instance ---
app = FastAPI(
    title="WebAI-to-API",
    description="A unified API for interacting with various Web-based AI models like Gemini and Claude.",
    version="0.1.0",
    lifespan=lifespan # Use the async context manager for startup/shutdown
)


# --- API Endpoints ---

@app.post("/claude", summary="Chat with Claude", operation_id="claude_chat")
async def claude_chat_endpoint(request_body: ClaudeRequest):
    """
    Endpoint for direct interaction with the Claude AI model.

    Allows sending a message to Claude and receiving a response, optionally streamed.
    The specific Claude model (e.g., Sonnet, Haiku) can be specified in the request.
    """
    if not ENABLED_AI["claude"] or not claude_client:
        logger.warning("Claude endpoint called but client is disabled or not initialized.")
        raise HTTPException(status_code=400, detail="Claude client is disabled or not initialized. Please check server configuration and logs.")

    try:
        if request_body.stream:
            # Ensure the stream_message method is an async generator
            async def stream_wrapper():
                async for chunk in claude_client.stream_message(request_body.message, request_body.model.value):
                    # Assuming chunk is already in a suitable format (e.g., JSON string for text/event-stream)
                    # Or convert to OpenAI chunk format if necessary
                    yield f"data: {json.dumps(convert_to_openai_format(chunk, request_body.model.value, stream=True))}\n\n"
                yield f"data: [DONE]\n\n" # Signal end of stream if client expects it

            return StreamingResponse(
                stream_wrapper(),
                media_type="text/event-stream", # Standard for SSE
            )
        else:
            # Non-streaming response
            response = await claude_client.send_message(request_body.message, request_body.model.value)
            # Return in a simple structure or convert to OpenAI format
            return {"response": response, "model_used": request_body.model.value}
    except Exception as e:
        logger.error(f"Error in Claude endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An error occurred with the Claude client: {str(e)}")

@app.post("/gemini", summary="Chat with Gemini", operation_id="gemini_chat")
async def gemini_chat_endpoint(request_body: GeminiRequest):
    """
    Endpoint for direct interaction with the Gemini AI model.

    Supports text messages and, if implemented by the client, image inputs.
    The specific Gemini model (e.g., Flash, Pro) can be specified.
    """
    if not ENABLED_AI["gemini"] or not gemini_client:
        logger.warning("Gemini endpoint called but client is disabled or not initialized.")
        raise HTTPException(status_code=400, detail="Gemini client is disabled or not initialized. Please check server configuration and logs.")

    try:
        # Gemini client's generate_content is expected to be async
        response = await gemini_client.generate_content(
            request_body.message,
            request_body.model.value, # Pass the enum value
            images=request_body.images if request_body.images else None # Pass images if provided
        )
        # Assuming response.text gives the main textual content
        return {"response": response.text, "model_used": request_body.model.value}
    except Exception as e:
        logger.error(f"Error in Gemini endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An error occurred with the Gemini client: {str(e)}")

@app.post("/deepseek", summary="Chat with Deepseek", operation_id="deepseek_chat")
async def deepseek_chat_endpoint(request_body: DeepseekRequest):
    """
    Endpoint for direct interaction with the Deepseek AI model.

    Allows sending a message and receiving a response, optionally streamed.
    The specific Deepseek model can be specified.
    """
    if not ENABLED_AI["deepseek"] or not deepseek_client:
        logger.warning("Deepseek endpoint called but client is disabled or not initialized.")
        raise HTTPException(status_code=400, detail="Deepseek client is disabled or not initialized. Please check server configuration and logs.")

    try:
        if request_body.stream:
            # Ensure the chat method is an async generator
            async def stream_wrapper():
                async for chunk in deepseek_client.chat(request_body.message, request_body.model.value, stream=True):
                    # Assuming chunk is a string or can be converted to a string
                    yield f"data: {json.dumps(convert_to_openai_format(str(chunk), request_body.model.value, stream=True))}\n\n"
                yield f"data: [DONE]\n\n"

            return StreamingResponse(
                stream_wrapper(),
                media_type="text/event-stream",
            )
        else:
            # Non-streaming: aggregate chunks if the client's non-stream chat is an async generator
            # Or call a specific non-streaming method if available
            response_content = ""
            # Assuming deepseek_client.chat with stream=False is an async generator or returns full content
            # Adjust if DeepseekClient has a different non-streaming interface
            async for chunk in deepseek_client.chat(request_body.message, request_body.model.value, stream=False): # Or stream=False
                response_content += str(chunk) # Make sure chunk is string
            return {"response": response_content, "model_used": request_body.model.value}
    except Exception as e:
        logger.error(f"Error in Deepseek endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An error occurred with the Deepseek client: {str(e)}")


@app.post("/v1/chat/completions", summary="OpenAI-compatible Chat Completions", operation_id="openai_chat_completions")
async def chat_completions_endpoint(request_body: OpenAIChatRequest, http_request: Request): # Added http_request for client info
    """
    OpenAI-compatible endpoint for chat completions.

    This endpoint mimics the OpenAI API structure, allowing it to be used as a
    drop-in replacement for applications designed for OpenAI. It routes requests
    to the appropriate backend AI (Claude, Gemini, or Deepseek) based on the
    `model` field or the `default_ai` setting in `config.conf`.
    """
    # Extract the last user message for simpler processing by some backends
    # More sophisticated handling might involve passing the whole history
    user_message_content = next((msg.content for msg in reversed(request_body.messages) if msg.role == "user"), None)
    if not user_message_content:
        logger.error(f"No user message found in request from {http_request.client.host if http_request.client else 'unknown client'}.")
        raise HTTPException(status_code=400, detail="No user message found in the 'messages' array.")

    # Determine target AI model
    target_model_name: Optional[str] = None
    target_client: Any = None
    actual_model_enum_value: Optional[Union[ClaudeModels, GeminiModels, DeepseekModels]] = request_body.model

    if request_body.model: # Model explicitly specified in request
        target_model_name = request_body.model.value # Get the string value from Enum
        if isinstance(request_body.model, ClaudeModels):
            if ENABLED_AI["claude"] and claude_client:
                target_client = claude_client
            else:
                raise HTTPException(status_code=400, detail=f"Claude model '{target_model_name}' requested, but Claude client is disabled or not initialized.")
        elif isinstance(request_body.model, GeminiModels):
            if ENABLED_AI["gemini"] and gemini_client:
                target_client = gemini_client
            else:
                raise HTTPException(status_code=400, detail=f"Gemini model '{target_model_name}' requested, but Gemini client is disabled or not initialized.")
        elif isinstance(request_body.model, DeepseekModels):
            if ENABLED_AI["deepseek"] and deepseek_client:
                target_client = deepseek_client
            else:
                raise HTTPException(status_code=400, detail=f"Deepseek model '{target_model_name}' requested, but Deepseek client is disabled or not initialized.")
        else:
            raise HTTPException(status_code=400, detail=f"Invalid 'model' specified: {request_body.model}. Does not match known model types.")
    else: # No model specified, use default_ai from config
        default_ai_service = config["AI"].get("default_ai", "gemini").lower() # Default to gemini if not set
        logger.info(f"No model specified in request, using default_ai: {default_ai_service}")
        if default_ai_service == "claude":
            if ENABLED_AI["claude"] and claude_client:
                target_client = claude_client
                # Use default Claude model from config
                actual_model_enum_value = ClaudeModels(config["AI"].get("default_model_claude", ClaudeModels.SONNET_5.value))
                target_model_name = actual_model_enum_value.value
            else:
                raise HTTPException(status_code=500, detail="Default AI (Claude) is disabled or not initialized.")
        elif default_ai_service == "gemini":
            if ENABLED_AI["gemini"] and gemini_client:
                target_client = gemini_client
                actual_model_enum_value = GeminiModels(config["AI"].get("default_model_gemini", GeminiModels.PRO.value))
                target_model_name = actual_model_enum_value.value
            else:
                raise HTTPException(status_code=500, detail="Default AI (Gemini) is disabled or not initialized.")
        elif default_ai_service == "deepseek":
            # Assuming Deepseek also has a default model in config if it were the default AI
            if ENABLED_AI["deepseek"] and deepseek_client:
                target_client = deepseek_client
                # You might need a config entry for default_model_deepseek
                actual_model_enum_value = DeepseekModels(config["AI"].get("default_model_deepseek", DeepseekModels.CHAT.value))
                target_model_name = actual_model_enum_value.value
            else:
                raise HTTPException(status_code=500, detail="Default AI (Deepseek) is disabled or not initialized.")
        else:
            raise HTTPException(status_code=400, detail=f"Default AI service '{default_ai_service}' in config is not supported.")

    if not target_client or not target_model_name or not actual_model_enum_value:
         raise HTTPException(status_code=500, detail="Could not determine target AI client or model name.")

    logger.info(f"Routing request from {http_request.client.host if http_request.client else 'unknown'} to {target_model_name} via /v1/chat/completions")

    try:
        if request_body.stream:
            # Common streaming logic for all clients
            async def stream_openai_response():
                client_stream = None
                if isinstance(actual_model_enum_value, ClaudeModels):
                    # Claude client's stream_message already yields OpenAI-like JSON strings.
                    # We just need to ensure they are sent as SSE events.
                    async for claude_chunk_json_str in target_client.stream_message(user_message_content, actual_model_enum_value.value):
                        # claude_chunk_json_str is already a JSON string formatted like an OpenAI chunk.
                        yield f"data: {claude_chunk_json_str}\n\n"
                elif isinstance(actual_model_enum_value, GeminiModels):
                    # Streaming for Gemini via gemini-webapi is not standard SSE.
                    # The gemini-webapi library's generate_content is not an async generator by default for SSE.
                    # If gemini-webapi has a true SSE streaming method, it should be adapted here.
                    # For now, simulating stream with a single chunk for Gemini as a fallback.
                    logger.warning(f"Gemini streaming for /v1/chat/completions is simulated as a single chunk for model {target_model_name}.")
                    gemini_response = await target_client.generate_content(user_message_content, actual_model_enum_value.value)
                    response_text = gemini_response.text
                    # Use the updated convert_to_openai_format with finish_reason_val
                    openai_chunk = convert_to_openai_format(response_text, target_model_name, stream=True, finish_reason_val="stop")
                    yield f"data: {json.dumps(openai_chunk)}\n\n"

                elif isinstance(actual_model_enum_value, DeepseekModels):
                    # Deepseek client's chat method yields raw strings which are expected to be JSON from SSE.
                    # We need to parse these and potentially re-format if they aren't already OpenAI compatible.
                    idx = 0
                    async for raw_chunk_str in target_client.chat(user_message_content, actual_model_enum_value.value, stream=True):
                        if not raw_chunk_str.strip(): continue # Skip empty keep-alive lines

                        # Attempt to parse, as Deepseek might send structured data (e.g. JSON lines)
                        # This part requires knowing the actual structure of Deepseek's SSE stream.
                        # Assuming for now it might send JSON that needs slight adaptation or is just text.
                        try:
                            # If Deepseek sends JSON chunks that are already OpenAI-like, we might pass them more directly.
                            # Example: chunk_data = json.loads(raw_chunk_str)
                            # content = chunk_data['choices'][0]['delta']['content']
                            # finish_reason = chunk_data['choices'][0]['finish_reason']
                            # For now, let's assume raw_chunk_str is the text content itself or needs simple parsing.
                            # This is a placeholder for more specific Deepseek stream handling.
                            # If Deepseek chunks are JSON like {"text": "foo", "is_final": false}
                            chunk_data = json.loads(raw_chunk_str)
                            content = chunk_data.get("text", chunk_data.get("completion", "")) # Adapt to actual key
                            is_final = chunk_data.get("is_final", False) # Hypothetical 'is_final'
                            finish_reason = "stop" if is_final else None

                            # Re-construct into OpenAI format if Deepseek's own format is different
                            # If Deepseek's format is simple text delta, convert_to_openai_format is fine.
                            # If it's structured, we adapt. Here, assuming a simple structure.
                            openai_formatted_chunk = convert_to_openai_format(
                                content, target_model_name, stream=True,
                                response_id_val=f"ds-chunk-{idx}", # More unique ID per chunk
                                finish_reason_val=finish_reason
                            )
                            yield f"data: {json.dumps(openai_formatted_chunk)}\n\n"
                            if finish_reason:
                                break
                        except json.JSONDecodeError:
                            # Fallback: if raw_chunk_str is not JSON, treat it as plain text delta
                            openai_formatted_chunk = convert_to_openai_format(
                                raw_chunk_str, target_model_name, stream=True,
                                response_id_val=f"ds-chunk-{idx}"
                            )
                            yield f"data: {json.dumps(openai_formatted_chunk)}\n\n"
                        idx += 1

                # General stream termination signal for all successful streams
                yield f"data: [DONE]\n\n"

            return StreamingResponse(stream_openai_response(), media_type="text/event-stream")
        else: # Non-streaming
            response_text = ""
            # Placeholder for actual token counts if they become available
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0

            if isinstance(actual_model_enum_value, ClaudeModels):
                # ClaudeClient.send_message returns the full text content
                response_text = await target_client.send_message(user_message_content, actual_model_enum_value.value)
                # Token info not available from this client
            elif isinstance(actual_model_enum_value, GeminiModels):
                gemini_response = await target_client.generate_content(user_message_content, actual_model_enum_value.value)
                response_text = gemini_response.text
                # Check if gemini_response object contains token info (hypothetical)
                # prompt_tokens = getattr(gemini_response, 'prompt_token_count', 0)
                # completion_tokens = getattr(gemini_response, 'completion_token_count', 0)
                # total_tokens = getattr(gemini_response, 'total_token_count', 0)
            elif isinstance(actual_model_enum_value, DeepseekModels):
                temp_response_parts = []
                # Deepseek client's chat with stream=False should ideally return full content or be aggregated.
                # The current Deepseek client's chat(stream=False) might still be an async generator.
                async for part in target_client.chat(user_message_content, actual_model_enum_value.value, stream=False):
                    # If stream=False still yields JSON strings for some reason:
                    try:
                        json_part = json.loads(part)
                        temp_response_parts.append(json_part.get("text", ""))
                        if json_part.get("is_final"): break # Hypothetical
                    except json.JSONDecodeError:
                        temp_response_parts.append(str(part))
                response_text = "".join(temp_response_parts)
                # Token info not available

            openai_response = convert_to_openai_format(response_text, target_model_name, stream=False)
            # Update with actual token counts if they were retrieved (currently placeholders)
            openai_response["usage"]["prompt_tokens"] = prompt_tokens
            openai_response["usage"]["completion_tokens"] = completion_tokens
            openai_response["usage"]["total_tokens"] = total_tokens
            return openai_response

            return StreamingResponse(stream_openai_response(), media_type="text/event-stream")
        else:
            # Non-streaming logic for all clients
            response_text = ""
            if isinstance(actual_model_enum_value, ClaudeModels):
                response_text = await target_client.send_message(user_message_content, actual_model_enum_value.value)
            elif isinstance(actual_model_enum_value, GeminiModels):
                # Assuming generate_content returns an object with a .text attribute
                gemini_response = await target_client.generate_content(user_message_content, actual_model_enum_value.value)
                response_text = gemini_response.text
            elif isinstance(actual_model_enum_value, DeepseekModels):
                # Assuming chat returns full text or an async generator that we need to aggregate
                # If stream=False returns full text directly:
                # response_text = await target_client.chat(user_message_content, actual_model_enum_value.value, stream=False)
                # If it's still an async generator even with stream=False:
                temp_response_parts = []
                async for part in target_client.chat(user_message_content, actual_model_enum_value.value, stream=False):
                    temp_response_parts.append(str(part))
                response_text = "".join(temp_response_parts)

            return convert_to_openai_format(response_text, target_model_name)

    except Exception as e:
        logger.error(f"Error in /v1/chat/completions for model {target_model_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An error occurred while processing your request with {target_model_name}: {str(e)}")


# --- Server Execution ---
def run_server(host: str, port: int, reload: bool):
    """
    Starts the Uvicorn server with the FastAPI application.

    Args:
        host: The hostname or IP address to bind the server to.
        port: The port number to listen on.
        reload: Whether to enable auto-reloading for development.
    """
    logger.info(f"Starting server at http://{host}:{port}")
    print(
        f"""
        ================================================================
        🚀 WebAI-to-API Server is starting! 🚀
        ================================================================
        Access the API documentation (Swagger UI): http://{host}:{port}/docs
        ----------------------------------------------------------------
        Project Repository: https://github.com/Amm1rr/WebAI-to-API/
        ================================================================
        """
    )
    try:
        import uvicorn
        uvicorn.run("main:app", host=host, port=port, reload=reload) # "main:app" string for reload
    except ImportError:
        logger.error("Uvicorn is not installed. Please install it with 'pip install uvicorn'.")
    except Exception as e:
        logger.error(f"An error occurred while trying to run the server: {str(e)}", exc_info=True)

if __name__ == "__main__":
    # Setup command-line argument parsing for server configuration
    parser = argparse.ArgumentParser(description="Run the WebAI-to-API FastAPI server.")
    parser.add_argument("--host", type=str, default="localhost", help="Hostname or IP address to bind to (default: localhost).")
    parser.add_argument("--port", type=int, default=6969, help="Port number to listen on (default: 6969).")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading for development (requires Uvicorn standard reload).")
    args = parser.parse_args()

    logger.info(f"Server launch arguments: host='{args.host}', port={args.port}, reload={args.reload}")
    run_server(args.host, args.port, args.reload)
