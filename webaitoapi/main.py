import os
import time
import json
import configparser
import logging
import asyncio
from enum import Enum
from contextlib import asynccontextmanager
from typing import Literal, Optional, List, Union

import browser_cookie3
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from models.gemini import MyGeminiClient

# -----------------------------
# Define available Gemini models
# -----------------------------
class GeminiModels(str, Enum):
    FLASH_1_5 = "gemini-1.5-flash"
    FLASH_2_0 = "gemini-2.0-flash"
    FLASH_THINKING = "gemini-2.0-flash-thinking"
    FLASH_THINKING_WITH_APPS = "gemini-2.0-flash-thinking-with-apps"

# -----------------------------
# Logging configuration
# -----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# Load and update configuration
# -----------------------------
config = configparser.ConfigParser()
config.read("config.conf")

if "Browser" not in config:
    config["Browser"] = {"name": "brave"}

if "Cookies" not in config:
    config["Cookies"] = {}

with open("config.conf", "w") as configfile:
    config.write(configfile)

# -----------------------------
# Check which AIs are enabled
# -----------------------------
ENABLED_AI = {
    "gemini": config.getboolean("EnabledAI", "gemini", fallback=True),
}

# -----------------------------
# Request schemas
# -----------------------------
class GeminiRequest(BaseModel):
    message: str
    model: GeminiModels = Field(default=GeminiModels.FLASH_2_0, description="Model to use for Gemini.")
    images: Optional[List[str]] = []

class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[Union[GeminiModels]] = None
    stream: Optional[bool] = False

# -----------------------------
# Global variables for client and session managers
# -----------------------------
gemini_client = None
translate_session_manager = None
gemini_chat_manager = None

# -----------------------------
# SessionManager for handling chat/translate sessions
# -----------------------------
class SessionManager:
    def __init__(self, client: MyGeminiClient):
        self.client = client
        self.session = None
        self.model = None
        self.lock = asyncio.Lock()

    async def get_response(self, model: GeminiModels, message: str, images: List[str]):
        async with self.lock:
            if self.session is None or self.model != model:
                if self.session is not None:
                    await self.session.close()
                self.session = self.client.start_chat(model=model)
                self.model = model
        return await self.session.send_message(message, images=images)

# -----------------------------
# Helper function: Get cookies from browser
# -----------------------------
def get_cookie_from_browser(service: Literal["gemini"]) -> Optional[tuple]:
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

# -----------------------------
# Lifespan for FastAPI application
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client, translate_session_manager, gemini_chat_manager

    if ENABLED_AI["gemini"]:
        try:
            gemini_cookie_1PSID = config["Cookies"].get("gemini_cookie_1PSID")
            gemini_cookie_1PSIDTS = config["Cookies"].get("gemini_cookie_1PSIDTS")
            if not gemini_cookie_1PSID or not gemini_cookie_1PSIDTS:
                cookies = get_cookie_from_browser("gemini")
                if cookies:
                    gemini_cookie_1PSID, gemini_cookie_1PSIDTS = cookies
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

    if gemini_client:
        translate_session_manager = SessionManager(gemini_client)
        gemini_chat_manager = SessionManager(gemini_client)

    yield

    if gemini_client:
        await gemini_client.close()
        logger.info("Gemini client closed successfully.")

# -----------------------------
# Create FastAPI app and add middleware
# -----------------------------
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Endpoints
# -----------------------------
# Endpoint for direct Gemini content generation
@app.post("/gemini")
async def gemini_generate(request: GeminiRequest):
    if not ENABLED_AI["gemini"]:
        raise HTTPException(status_code=400, detail="Gemini client is disabled.")
    if not gemini_client:
        raise HTTPException(status_code=500, detail="Gemini client is not initialized.")
    try:
        response = await gemini_client.generate_content(request.message, request.model, images=request.images)
        return {"response": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint for translation using a session manager
@app.post("/translate")
async def translate_chat(request: GeminiRequest):
    if not ENABLED_AI["gemini"]:
        raise HTTPException(status_code=400, detail="Gemini client is disabled.")
    if not gemini_client or not translate_session_manager:
        raise HTTPException(status_code=500, detail="Gemini client is not initialized.")
    try:
        response = await translate_session_manager.get_response(request.model, request.message, request.images)
        return {"response": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint for Gemini chat using a session manager
@app.post("/gemini-chat")
async def gemini_chat_session_endpoint(request: GeminiRequest):
    if not ENABLED_AI["gemini"]:
        raise HTTPException(status_code=400, detail="Gemini client is disabled.")
    if not gemini_client or not gemini_chat_manager:
        raise HTTPException(status_code=500, detail="Gemini client is not initialized.")
    try:
        response = await gemini_chat_manager.get_response(request.model, request.message, request.images)
        return {"response": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# OpenAI-compatible endpoint
@app.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatRequest):
    user_message = next((msg["content"] for msg in request.messages if msg["role"] == "user"), None)
    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found in the request.")

    if request.model:
        if isinstance(request.model, GeminiModels):
            if not ENABLED_AI["gemini"]:
                raise HTTPException(status_code=400, detail="Gemini client is disabled.")
            if not gemini_client:
                raise HTTPException(status_code=500, detail="Gemini client is not initialized.")
            try:
                response = await gemini_client.generate_content(user_message, request.model.value)
                return convert_to_openai_format(response.text, request.model.value, request.stream)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail="Invalid model specified. Choose a valid model.")

# Helper function to convert Gemini response into OpenAI format
def convert_to_openai_format(response: str, model: str, stream: bool = False):
    return {
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

# -----------------------------
# Server runner
# -----------------------------
def run_server(args):
    logging.debug("main.py.run_server()")
    print(
        """
        Welcome to, WebAI to API:
        
        Swagger UI (Docs)  : http://localhost:6969/docs
        
        ----------------------------------------------------------------
        
        About:
            Learn more about the project: https://github.com/amm1rr/WebAI-to-API/
        """
    )
    try:
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    logging.info(__name__ + ".name()")
    import argparse
    parser = argparse.ArgumentParser(description="Run the server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=6969, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()
    run_server(args)
