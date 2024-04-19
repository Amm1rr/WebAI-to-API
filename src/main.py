# Standard Library Imports
import argparse
import configparser
import json
import os
import sys
import time
import utility
import urllib.parse

# Third-Party Imports
import uvicorn

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from h11 import Response
from pydantic import BaseModel
from anyio import Path

# Local Imports
import gemini
import claude

# UI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

#############################################
####                                     ####
#####          Global Initilize         #####
####                                     ####

"""Config file name and paths for chatbot API configuration."""
CONFIG_FILE_NAME = "Config.conf"
CONFIG_FOLDER = os.getcwd()

"""Disable search on files cookie(fix 'PermissionError: [Errno 1] Operation not permitted') now used only for Claude"""
ISCONFIGONLY = False

# CONFIG_FOLDER = os.path.expanduser("~/.config")
# CONFIG_FOLDER = Path(CONFIG_FOLDER) / "WebAI_to_API"



FixConfigPath = lambda: (
    Path(CONFIG_FOLDER) / CONFIG_FILE_NAME
    if os.path.basename(CONFIG_FOLDER).lower() == "src"
    else Path(CONFIG_FOLDER) / "src" / CONFIG_FILE_NAME
)

"""Path to API configuration file."""
CONFIG_FILE_PATH = FixConfigPath()

def ResponseModel():
    config = configparser.ConfigParser()
    config.read(filenames=CONFIG_FILE_PATH)
    return config.get("Main", "Model", fallback="Claude")

OpenAIResponseModel = ResponseModel()


""" Initialization AI Models and Cookies """
COOKIE_CLAUDE = utility.getCookie_Claude(configfilepath=CONFIG_FILE_PATH, configfilename=CONFIG_FILE_NAME) #message.session_id
COOKIE_GEMINI = utility.getCookie_Gemini(configfilepath=CONFIG_FILE_PATH, configfilename=CONFIG_FILE_NAME) #message.session_id
GEMINI_CLIENT = gemini.GeminiInit()
CLAUDE_CLIENT = claude.Client(COOKIE_CLAUDE)

"""FastAPI application instance."""

app = FastAPI()

# Add CORS middleware to allow all origins, credentials, methods, and headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


"""Request message data model."""


class MessageClaude(BaseModel):
    message: str
    stream: bool = True


class MessageGemini(BaseModel):
    message: str
    stream: bool = False

class Message(BaseModel):
    message: str
    stream: bool = False


#############################################
####                                     ####
#####             The Gemini            #####
####                                     ####

@app.post("/gemini")
async def ask_gemini(request: Request, message: MessageGemini):
    """API endpoint to get response from Google Gemini.

    Args:
        request (Request): API request object
        message (Message): Message request object

    Returns:
        str: Gemini response

    Raises:
        ConnectionError: If internet connection or API server is unavailable
        HTTPError: If HTTP error response received from API
        RequestException: If other request error occurs
        Exception: For any other errors

    """
    
    if not GEMINI_CLIENT:
        return {"warning": "Looks like you're not logged in to Gemini. Please either set the Gemini cookie manually or log in to your gemini.google.com account through your web browser."}
    
    if not message.message:
        message.message = "Hi, Who are you?"
    
    conversation_id = None

    if message.stream:
        try:
            # این شرط رو برای حالت غیر Stream نزاشتم چون در اون حالت خطای بهتری رو نشون میده اگر که اینترنت مشکل داشته باشه.
            # if not chatbot.SNlM0e:
            #     return {"Error": "Check the Gemini session."}


            if message.stream:
                res = GEMINI_CLIENT.ask_gemini(message=message.message)
                # print(res)
                return StreamingResponse(
                        res,
                        media_type="text/event-stream",
                    )
            else:
                res = await GEMINI_CLIENT.ask_geminiStream(message=message.message)
                # print(res)
                return res
        
        except requests.exceptions.ConnectionError:
            # Handle the ConnectionError exception here
            print(
                "Connection error occurred. Please check your internet connection or the server's availability."
            )
            return "Connection error occurred. Please check your internet connection or the server's availability."

        except requests.exceptions.HTTPError as http_err:
            # Handle HTTPError (e.g., 404, 500) if needed
            print(f"HTTP error occurred: {http_err}")
            return f"HTTP error occurred: {http_err}"

        except requests.exceptions.RequestException as req_err:
            # Handle other request exceptions if needed
            print(f"Request error occurred: {req_err}")
            return f"Request error occurred: {req_err}"

        except Exception as req_err:
            print(f"Error Occurred: {req_err}")
            return f"Error Occurred: {req_err}"

    else:
        try:
            response = GEMINI_CLIENT.ask_gemini(message.message)
            # print (response)
            return (response)
            # print(response["choices"][0]["message"]["content"][0])
            # return response["choices"][0]["message"]["content"][0]
        except requests.exceptions.ConnectionError:
            # Handle the ConnectionError exception here
            print(
                "Connection error occurred. Please check your internet connection or the server's availability."
            )
            return "Connection error occurred. Please check your internet connection or the server's availability."

        except requests.exceptions.HTTPError as http_err:
            # Handle HTTPError (e.g., 404, 500) if needed
            print(f"HTTP error occurred: {http_err}")
            return f"HTTP error occurred: {http_err}"

        except requests.exceptions.RequestException as req_err:
            # Handle other request exceptions if needed
            print(f"Request error occurred: {req_err}")
            return f"Request error occurred: {req_err}"

        except Exception as req_err:
            print(f"Error Occurred: {req_err}")
            return f"Error Occurred: {req_err}"


#############################################
####                                     ####
#####              Claude 3             #####
####                                     ####

@app.post("/claude")
async def ask_claude(request: Request, message: MessageClaude):
    """API endpoint to get Claude response.

    Args:
        request (Request): API request object.
        message (Message): Message request object.

    Returns:
        str: JSON string of Claude response.

    """

    if not COOKIE_CLAUDE:
        # cookie = os.environ.get("CLAUDE_COOKIE")
        return {"warning": "Looks like you're not logged in to Claude. Please either set the Claude cookie manually or log in to your Claude.ai account through your web browser."}
    
    conversation_id = None

    try:
        if not conversation_id:
            conversation = CLAUDE_CLIENT.create_new_chat()
            conversation_id = conversation["uuid"]
    except Exception as e:
        print(conversation)
        return ("error: ", conversation)

    if not message.message:
        message.message = "Hi, Who are you?"

    if message.stream:
        res = CLAUDE_CLIENT.stream_message(message.message, conversation_id)
        # print(res)
        return StreamingResponse(
                res,
                media_type="text/event-stream",
            )
    else:
        res = CLAUDE_CLIENT.send_message(message.message, conversation_id)
        # print(res)
        return res


#############################################
####           Claude/Gemini to          ####
#####       ChatGPT JSON Response       #####
####        `/v1/chat/completions`       ####

@app.post("/v1/chat/completions")
async def ask_ai(request: Request, message: Message):
    """API endpoint to get ChatGPT JSON response.

    Args:
        request (Request): API request object.
        message (Message): Message request object.
        model (String): Model name string.

    Returns:
        str: JSON string of ChatGPT JSON response.
    
    WebUI Configuration:
        Open http://localhost:8000/WebAI to configuration

    """
    
    OpenAIResponseModel = ResponseModel()

    if not message.message:
        message.message = "Hi, Who are you?"
    
    conversation_id = None
    
    if OpenAIResponseModel == "Gemini":
        
        if not GEMINI_CLIENT:
            return {"warning": "Looks like you're not logged in to Gemini. Please either set the Gemini cookie manually or log in to your gemini.google.com account through your web browser."}
        
        if message.stream:
            response = GEMINI_CLIENT.ask_geminiStream(message=message)
            ResponseToOpenAI = utility.ConvertToChatGPTStream(message=response, model=OpenAIResponseModel)
            return StreamingResponse(
                ResponseToOpenAI,
                media_type="text/event-stream",
            )
        else:
            response = GEMINI_CLIENT.ask_gemini(message.message)
            ResponseToOpenAI = utility.ConvertToChatGPT(message=response, model=OpenAIResponseModel)
            return ResponseToOpenAI
    
    else:
        
        if not CLAUDE_CLIENT:
            # cookie = os.environ.get("CLAUDE_COOKIE")
            return {"warning": "Looks like you're not logged in to Claude. Please either set the Claude cookie manually or log in to your Claude.ai account through your web browser."}
        
        conversation_id = None

        try:
            if not conversation_id:
                conversation = CLAUDE_CLIENT.create_new_chat()
                conversation_id = conversation["uuid"]
        except Exception as e:
            print(conversation)
            return ("ERROR: ", conversation)

        if message.stream:
            res = CLAUDE_CLIENT.stream_message(message.message, conversation_id)

            resjson = utility.ConvertToChatGPTStream(message=res, model="claude")
            # print(res)
            return StreamingResponse(
                    resjson,
                    media_type="text/event-stream",
                )
        else:
            res = CLAUDE_CLIENT.send_message(message.message, conversation_id)
            resjson = utility.ConvertToChatGPT(message=res, model="claude")
            # print(resjson)
            return resjson


#############################################
####                                     ####
#####          Web UI Middleware        #####
####                                     ####

index_html_path = os.path.join(os.path.dirname(__file__), "UI/build/index.html")
app.mount('/', StaticFiles(directory="src/UI/build"), 'static')

@app.middleware("http")

async def catch_all_endpoints(request: Request, call_next):
    response = await call_next(request)
    url = request.url.path.lower()
    if response.status_code == 404 and url == "/webai":
        index_html_path = os.path.join(os.path.dirname(__file__), "UI/build/index.html")
        return FileResponse(index_html_path)
    elif url == "/api/config":
        if os.path.exists(CONFIG_FILE_PATH):
            # print(utility.ConfigINI_to_Dict(CONFIG_FILE_PATH))
            return JSONResponse(json.dumps(utility.ConfigINI_to_Dict(CONFIG_FILE_PATH)), status_code=200)
            # return FileResponse(CONFIG_FILE_PATH)
        else:
            return JSONResponse({"error": CONFIG_FILE_PATH + " Config file not found"})
        # 
    elif url == "/api/config/getclaudekey":
        
        cookie = utility.getCookie_Claude(configfilepath=CONFIG_FILE_PATH, configfilename=CONFIG_FILE_NAME)
        if (cookie):
            return JSONResponse({"Claude": f"{cookie}"},  status_code=200)
        return JSONResponse({"warning": "Failed to get claude key"})
        
    elif url == "/api/config/getgeminikey":
        cookie = utility.getCookie_Gemini(configfilepath=CONFIG_FILE_PATH, configfilename=CONFIG_FILE_NAME)
        if (cookie):
            return JSONResponse(cookie, status_code=200)
        return JSONResponse({"warning": "Failed to get gemini key"})
    
    elif url == "/api/config/save":
        try:
            request_body = await request.json()
            model_name = request_body.get('Model')
            OpenAIResponseModel = model_name

            if not model_name:
                return JSONResponse({"error": model_name + " model not provided in request body"}, status_code=400)

            config = configparser.ConfigParser()
            config['Main'] = {}
            config['Main']['model'] = model_name

            with open(CONFIG_FILE_PATH, 'w') as configfile:
                config.write(configfile)

            return JSONResponse({"message": f"{model_name} saved successfully"}, status_code=200)
        except Exception as e:
            return JSONResponse({"error": f"Failed to save model: {str(e)}"}, status_code=500)
    
    return response

#############################################
####                                     ####
#####               Main                #####
####                                     ####

if __name__ == "__main__":
    """Parse arguments and run the UVicorn server.

    This allows running the FastAPI server from the command line
    by specifying the host, port, and whether to enable auto-reloading.

    Example:
        python main.py --host localhost --port 8000 --reload
            OR
        python main.py

    """
    parser = argparse.ArgumentParser(description="Run the UVicorn server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()
    
    print(
        """
        * WebAI to API:
            Configuration      : http://localhost:8000/WebAI
            Swagger UI (Docs)  : http://localhost:8000/docs
            ----------------------------------------------------------------
        * About:
                https://github.com/amm1rr/WebAI-to-API/
        """,
    )
    
    uvicorn.run("main:app", host=args.host, port=args.port, reload=args.reload)

    ##### TO USE HTTPS
    ###
    # from subprocess import Popen
    # Popen(["python", "-m", "https_redirect"])  # Add this
    # uvicorn.run(
    #     "main:app",
    #     host=args.host,
    #     port=args.port,
    #     reload=args.reload,
    #     reload_dirs=["html_files"],
    #     ssl_keyfile="/etc/letsencrypt/live/my_domain/privkey.pem",
    #     ssl_certfile="/etc/letsencrypt/live/my_domain/fullchain.pem",
    # )
