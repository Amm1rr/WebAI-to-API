# Standard Library Imports
import argparse
import os
import uvicorn
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Local Imports
from .models import claude
from gemini_webapi import GeminiClient
from .utils import utility

utility.configure_logging()
logging.debug("main.py")

def Config_UI_Path():
    config_ui_path = None
    root_path = os.getcwd()
    if "webai2api/webai2api" in root_path:
        config_ui_path = os.path.join(os.path.dirname(__file__), "webai2api/UI/build/index.html")
    else:
        config_ui_path = os.path.join(os.path.dirname(__file__), "UI/build/index.html")

    logging.debug("main.py:Config_UI_Path(): ", config_ui_path)
    return config_ui_path

# Constants
CONFIG_FILE_NAME = "Config.conf"
CONFIG_FOLDER = os.getcwd()
if "/webai2api" not in CONFIG_FOLDER:
    CONFIG_FOLDER += "/webai2api"
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, CONFIG_FILE_NAME)

# FastAPI application instance
app = FastAPI()

# Global variables
COOKIE_CLAUDE = None
COOKIE_GEMINI = None
GEMINI_CLIENT = None
CLAUDE_CLIENT = None

# Initialize AI models and cookies
async def initialize_ai_models(config_file_path: str):
    global COOKIE_CLAUDE, COOKIE_GEMINI, GEMINI_CLIENT, CLAUDE_CLIENT
    COOKIE_CLAUDE = utility.getCookie_Claude(configfilepath=config_file_path, configfilename=CONFIG_FILE_NAME)
    COOKIE_GEMINI = utility.getCookie_Gemini(configfilename=CONFIG_FILE_NAME)
    CLAUDE_CLIENT = claude.Client(COOKIE_CLAUDE)
    GEMINI_CLIENT = GeminiClient()
    try:
        await GEMINI_CLIENT.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True, verbose=False)
    except Exception as e:
        print(e)

# Startup event handler
async def startup():
    await initialize_ai_models(CONFIG_FILE_PATH)

app.add_event_handler("startup", startup)

# Middleware for CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Run UVicorn server
def run_server(args):
    logging.debug("main.py./run_server()")
    print(
        """
        
        Welcome to WebAI to API:

        Configuration      : http://localhost:8000/WebAI
        Swagger UI (Docs)  : http://localhost:8000/docs
        
        ----------------------------------------------------------------
        
        About:
            Learn more about the project: https://github.com/amm1rr/WebAI-to-API/
        
        """
    )
    # print("Welcome to WebAI to API:\n\nConfiguration      : http://localhost:8000/WebAI\nSwagger UI (Docs)  : http://localhost:8000/docs\n\n----------------------------------------------------------------\n\nAbout:\n    Learn more about the project: https://github.com/amm1rr/WebAI-to-API/\n")
    uvicorn.run("main:app", host=args.host, port=args.port, reload=args.reload)

# Main function
def main():
    logging.debug("main.py./main()")
    parser = argparse.ArgumentParser(description="Run the server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()
    run_server(args)

if __name__ == "__main__":
    main()
    logging.debug("main.py./__name__()")