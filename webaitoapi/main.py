import os
import time
import json
import configparser
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Union
from contextlib import asynccontextmanager
import logging
import browser_cookie3
from enum import Enum
from models.gemini import MyGeminiClient
from fastapi.middleware.cors import CORSMiddleware

from contextlib import asynccontextmanager
import asyncio

# Define available models for each AI
class GeminiModels(str, Enum):
    FLASH_1_5 = "gemini-1.5-flash"
    FLASH_2_0 = "gemini-2.0-flash"
    FLASH_THINKING = "gemini-2.0-flash-thinking"
    FLASH_THINKING_WITH_APPS = "gemini-2.0-flash-thinking-with-apps"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load configuration
config = configparser.ConfigParser()
config.read("config.conf")

# Set default browser if not specified
if "Browser" not in config:
    config["Browser"] = {"name": "brave"}

# Set default Cookies section if not specified
if "Cookies" not in config:
    config["Cookies"] = {}

# Write the updated config to file
with open("config.conf", "w") as configfile:
    config.write(configfile)

# Check which AIs are enabled
ENABLED_AI = {
    "gemini": config.getboolean("EnabledAI", "gemini", fallback=True),
}

# Define request schemas
class GeminiRequest(BaseModel):
    message: str
    model: GeminiModels = Field(default=GeminiModels.FLASH_2_0, description="Model to use for Gemini.")
    images: Optional[List[str]] = []

class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[Union[GeminiModels]] = None
    stream: Optional[bool] = False

# Initialize AI clients
gemini_client = None

# Translation session variables
translate_chat_session = None
translate_chat_model = None
translate_chat_lock = asyncio.Lock()

# Gemini Chat session variables
gemini_chat_model = None
gemini_chat_session = None
gemini_chat_lock = asyncio.Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client

    # Initialize Gemini client if enabled
    if ENABLED_AI["gemini"]:
        try:
            gemini_cookie_1PSID = config["Cookies"].get("gemini_cookie_1PSID")
            gemini_cookie_1PSIDTS = config["Cookies"].get("gemini_cookie_1PSIDTS")
            if not gemini_cookie_1PSID or not gemini_cookie_1PSIDTS:
                gemini_cookie_1PSID, gemini_cookie_1PSIDTS = get_cookie_from_browser("gemini")
            if gemini_cookie_1PSID and gemini_cookie_1PSIDTS:
                gemini_client = MyGeminiClient(gemini_cookie_1PSID, gemini_cookie_1PSIDTS)
                await gemini_client.init()
                logger.info("Gemini client initialized successfully.")
            else:
                logger.warning("Gemini cookies not found. Gemini API will not be available.")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            gemini_client = None
    else:
        logger.info("Gemini client is disabled. Skipping initialization.")

    yield

    # Cleanup
    if gemini_client:
        await gemini_client.close()
        logger.info("Gemini client closed successfully.")

# Helper function to get cookies from browser
def get_cookie_from_browser(service: Literal["gemini"]) -> tuple:
    browser_name = config["Browser"].get("name", "firefox").lower()
    logger.info(f"Attempting to get cookies from browser: {browser_name} for service: {service}")

    try:
        if browser_name == "firefox":
            cookies = browser_cookie3.firefox()
        elif browser_name == "chrome":
            cookies = browser_cookie3.chrome()
        elif browser_name == "brave":
            cookies = browser_cookie3.brave()
        elif browser_name == "edge":
            cookies = browser_cookie3.edge()
        elif browser_name == "safari":
            cookies = browser_cookie3.safari()
        else:
            raise ValueError(f"Unsupported browser: {browser_name}")
        
        logger.info(f"Successfully retrieved cookies from {browser_name}")
    except Exception as e:
        logger.error(f"Failed to retrieve cookies from {browser_name}: {e}")
        return None

    if service == "gemini":
        logger.info("Looking for Gemini cookies (__Secure-1PSID and __Secure-1PSIDTS)...")
        secure_1psid = None
        secure_1psidts = None
        for cookie in cookies:
            if cookie.name == "__Secure-1PSID" and "google" in cookie.domain:
                secure_1psid = cookie.value
                logger.info(f"Found __Secure-1PSID: {secure_1psid}")
            elif cookie.name == "__Secure-1PSIDTS" and "google" in cookie.domain:
                secure_1psidts = cookie.value
                logger.info(f"Found __Secure-1PSIDTS: {secure_1psidts}")
        if secure_1psid and secure_1psidts:
            logger.info("Both Gemini cookies found.")
            return secure_1psid, secure_1psidts
        else:
            logger.warning("Gemini cookies not found or incomplete.")
            return None
    else:
        logger.warning(f"Unsupported service: {service}")
        return None

# Create FastAPI app
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gemini endpoint
@app.post("/gemini")
async def gemini_chat(request: GeminiRequest):
    if not ENABLED_AI["gemini"]:
        raise HTTPException(status_code=400, detail="Gemini client is disabled.")
    if not gemini_client:
        raise HTTPException(status_code=500, detail="Gemini client is not initialized.")

    try:
        response = await gemini_client.generate_content(request.message, request.model, images=request.images)
        return {"response": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Translate endpoint
@app.post("/translate")
async def translate_chat(request: GeminiRequest):
    global translate_chat_session, translate_chat_model, translate_chat_lock

    if not ENABLED_AI["gemini"]:
        raise HTTPException(status_code=400, detail="Gemini client is disabled.")
    if not gemini_client:
        raise HTTPException(status_code=500, detail="Gemini client is not initialized.")

    try:
        async with translate_chat_lock:
            # Check if model changed or session not initialized
            if (
                translate_chat_session is None
                or translate_chat_model != request.model
            ):
                # Close previous session if exists
                if translate_chat_session is not None:
                    await translate_chat_session.close()
                
                # Start new session with selected model
                translate_chat_session = gemini_client.start_chat(model=request.model)
                translate_chat_model = request.model

        # Send message with images to existing session
        response = await translate_chat_session.send_message(
            request.message, 
            images=request.images
        )
        return {"response": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Translate endpoint
@app.post("/gemini-chat")
async def gemini_chat(request: GeminiRequest):
    global gemini_chat_session, gemini_chat_model, gemini_chat_lock

    if not ENABLED_AI["gemini"]:
        raise HTTPException(status_code=400, detail="Gemini client is disabled.")
    if not gemini_client:
        raise HTTPException(status_code=500, detail="Gemini client is not initialized.")

    try:
        async with gemini_chat_lock:
            # Check if model changed or session not initialized
            if (
                gemini_chat_session is None
                or gemini_chat_model != request.model
            ):
                # Close previous session if exists
                if gemini_chat_session is not None:
                    await gemini_chat_session.close()
                
                # Start new session with selected model
                gemini_chat_session = gemini_client.start_chat(model=request.model)
                gemini_chat_model = request.model

        # Send message with images to existing session
        response = await gemini_chat_session.send_message(
            request.message, 
            images=request.images
        )
        return {"response": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# OpenAI-compatible endpoint
@app.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatRequest):
    # Extract the last user message
    user_message = next((msg["content"] for msg in request.messages if msg["role"] == "user"), None)
    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found in the request.")

    # Determine which AI to use based on the model
    if request.model:
        if isinstance(request.model, GeminiModels):
            if not ENABLED_AI["gemini"]:
                raise HTTPException(status_code=400, detail="Gemini client is disabled.")
            if not gemini_client:
                raise HTTPException(status_code=500, detail="Gemini client is not initialized.")
            try:
                response = await gemini_client.generate_content(user_message, request.model.value)
                return convert_to_openai_format(response.text, request.model.value)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail="Invalid model specified. Choose a valid model.")

# Convert response to OpenAI format
def convert_to_openai_format(response: str, model: str, stream: bool = False):
    openai_response = {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk" if stream else "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
    return openai_response



# Run uvicorn server
def run_server(args):
    logging.debug("main.py./run_server()")
    print(
        """
        
        Welcome to, WebAI to API:

        Swagger UI (Docs)  : http://localhost:6969/docs
        
        ----------------------------------------------------------------
        
        About:
            Learn more about the project: https://github.com/amm1rr/WebAI-to-API/
        
        """
    )
    # print("Welcome to WebAI to API:\n\nConfiguration      : http://localhost:6969/WebAI\nSwagger UI (Docs)  :
    # http://localhost:6969/docs\n\n----------------------------------------------------------------\n\nAbout:\n
    # Learn more about the project: https://github.com/amm1rr/WebAI-to-API/\n")
    try:
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")


# Run the app
if __name__ == "__main__":
    logging.info(__name__ + ".name()")
    import argparse
    parser = argparse.ArgumentParser(description="Run the server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=6969, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()
    run_server(args)
