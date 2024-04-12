# Standard Library Imports
import argparse
import configparser
import json
import os
import sys
import time

import urllib.parse

import utility

# Third-Party Imports
import uvicorn

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from h11 import Response
from pydantic import BaseModel

# Local Imports
from bard import ChatbotGemini
from claude import Client
from anyio import Path


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


class Message(BaseModel):
    message: str
    stream: bool = True


class MessageBard(BaseModel):
    message: str
    stream: bool = True


#############################################
####                                     ####
#####             The Gemini            #####
####                                     ####


@app.post("/gemini")
async def ask_gemini(request: Request, message: MessageBard):
    """API endpoint to get response from Anthropic's Claude/Bard.

    Args:
        request (Request): API request object
        message (Message): Message request object

    Returns:
        str: Bard response

    Raises:
        ConnectionError: If internet connection or API server is unavailable
        HTTPError: If HTTP error response received from API
        RequestException: If other request error occurs
        Exception: For any other errors

    """
    # Execute code without authenticating the resource
    session_id = None #message.session_id
    session_idTS = None #message.session_idTS
    session_idCC = None #message.session_idCC
    # if not utility.IsSession(session_id):
    #     session_id = os.getenv("SESSION_ID")
    #     # print("Session: " + str(session_id) if session_id is not None else "Session ID is not available.")
    cookies = None
    

    #if not session_id:
    #    session_id = get_session_id_Bard("SESSION_ID")

    #if not session_idTS:
    #    session_idTS = get_session_id_Bard("SESSION_IDTS")
    
    #if not session_idCC:
    #   session_idCC = get_session_id_Bard("SESSION_IDCC")
    gemini = None
    if not (session_id or session_idTS or session_idCC):
      cookies = ChatbotGemini.get_session_id_Bard()
      if type(cookies) == dict:
        gemini = ChatbotGemini(cookies)
      else:
        gemini = ChatbotGemini(session_id=session_id, session_idTS=session_idTS, session_idCC=session_idCC)
        
    else:
      gemini = ChatbotGemini(session_id=session_id, session_idTS=session_idTS, session_idCC=session_idCC)
    
    if not message.message:
        message.message = "Hi, are you there?"
    
    conversation_id = None

    if message.stream:
        # try:
            # این شرط رو برای حالت غیر Stream نزاشتم چون در اون حالت خطای بهتری رو نشون میده اگر که اینترنت مشکل داشته باشه.
            # if not chatbot.SNlM0e:
            #     return {"Error": "Check the Bard session."}


            if message.stream:
                res = gemini.ask_bard(message=message.message)
                # print(res)
                return StreamingResponse(
                        res,
                        media_type="text/event-stream",
                    )
            else:
                res = await gemini.ask_bardStream(message=message.message)
                # print(res)
                return res
        
        # except requests.exceptions.ConnectionError:
        #     # Handle the ConnectionError exception here
        #     print(
        #         "Connection error occurred. Please check your internet connection or the server's availability."
        #     )
        #     return "Connection error occurred. Please check your internet connection or the server's availability."

        # except requests.exceptions.HTTPError as http_err:
        #     # Handle HTTPError (e.g., 404, 500) if needed
        #     print(f"HTTP error occurred: {http_err}")
        #     return f"HTTP error occurred: {http_err}"

        # except requests.exceptions.RequestException as req_err:
        #     # Handle other request exceptions if needed
        #     print(f"Request error occurred: {req_err}")
        #     return f"Request error occurred: {req_err}"

        # except Exception as req_err:
        #     print(f"Error Occurred: {req_err}")
        #     return f"Error Occurred: {req_err}"

    else:
        # try:
            response = gemini.ask_bard(message.message)
            # print (response)
            return (response)
            # print(response["choices"][0]["message"]["content"][0])
            # return response["choices"][0]["message"]["content"][0]
        # except requests.exceptions.ConnectionError:
        #     # Handle the ConnectionError exception here
        #     print(
        #         "Connection error occurred. Please check your internet connection or the server's availability."
        #     )
        #     return "Connection error occurred. Please check your internet connection or the server's availability."

        # except requests.exceptions.HTTPError as http_err:
        #     # Handle HTTPError (e.g., 404, 500) if needed
        #     print(f"HTTP error occurred: {http_err}")
        #     return f"HTTP error occurred: {http_err}"

        # except requests.exceptions.RequestException as req_err:
        #     # Handle other request exceptions if needed
        #     print(f"Request error occurred: {req_err}")
        #     return f"Request error occurred: {req_err}"

        # except Exception as req_err:
        #     print(f"Error Occurred: {req_err}")
        #     return f"Error Occurred: {req_err}"


#############################################
####                                     ####
#####              Claude 3             #####
####                                     ####

@app.post("/claude")
async def ask_claude(request: Request, message: Message):
    """API endpoint to get Claude response.

    Args:
        request (Request): API request object.
        message (Message): Message request object.

    Returns:
        str: JSON string of Claude response.

    """
    cookie = None #message.session_id

    # if not cookie:
    #     cookie = os.environ.get("CLAUDE_COOKIE")

    if not cookie:
        # if error by system(permission denided)
        try:
            if ISCONFIGONLY:
                raise Exception()
            cookie = utility.get_Cookie("Claude")
            if not cookie:
                raise Exception()
        except Exception as _:
            config = configparser.ConfigParser()
            config.read(filenames=CONFIG_FILE_PATH)
            cookie = config.get("Claude", "COOKIE", fallback=None)
            if not cookie:
                response_error = {
                    "Error": f"You should set 'COOKIE' in '{CONFIG_FILE_NAME}' file for the Bard or send it as an argument."
                }

                print(response_error)
                return response_error
                            # raise ValueError(
                            #     f"You should set 'COOKIE' in '{CONFIG_FILE_NAME}' file for the Bard or send it as an argument."
                            # )
    
    claude = Client(cookie)
    conversation_id = None

    try:
        if not conversation_id:
            conversation = claude.create_new_chat()
            conversation_id = conversation["uuid"]
    except Exception as e:
        print(conversation)
        return ("ERROR: ", conversation)

    if not message.message:
        message.message = "Hi, are you there?"

    if message.stream:
        res = claude.stream_message(message.message, conversation_id)
        # print(res)
        return StreamingResponse(
                res,
                media_type="text/event-stream",
            )
    else:
        res = claude.send_message(message.message, conversation_id)
        # print(res)
        return res


#############################################
####                                     ####
######      ChatGPT JSON Response      ######
####        `/v1/chat/completions`       ####

@app.post("/v1/chat/completions")
async def ask_ai(request: Request, message: Message, model: str):
    """API endpoint to get ChatGPT JSON response.

    Args:
        request (Request): API request object.
        message (Message): Message request object.
        model (String): Model name string.

    Returns:
        str: JSON string of ChatGPT JSON response.

    """

     # Execute code without authenticating the resource
    session_id = None #message.session_id
    session_idTS = None #message.session_idTS
    session_idCC = None #message.session_idCC

    gemini = None
    if not (session_id or session_idTS or session_idCC):
      cookies = Bard.get_session_id_Bard()
      if type(cookies) == dict:
        gemini = ChatbotGemini(cookies)
      else:
        gemini = ChatbotGemini(session_id=session_id, session_idTS=session_idTS, session_idCC=session_idCC)
        
    else:
      gemini = ChatbotGemini(session_id=session_id, session_idTS=session_idTS, session_idCC=session_idCC)
    
    if not message.message:
        message.message = "Hi, are you there?"
    
    conversation_id = None

    if not message.message:
        message.message = "Hi, are you there?"
    
    if not model == "claude":
        model = "gemini"
    
        if message.stream:
            response = await ask_gemini(request=Request,message=message)
            ResponseToOpenAI = ConvertToChatGPT(request=Request,message=response, model="gemini")
            return StreamingResponse(
                ResponseToOpenAI,
                media_type="text/event-stream",
            )
        else:
            response = gemini.ask_bard(message.message)
            ResponseToOpenAI = ConvertToChatGPT(request=Request,message=response, model="gemini")
            return ResponseToOpenAI

async def ConvertToChatGPT(request: Request, message: str, model: str):
    """Convert response to ChatGPT JSON format.

    Args:
        message (String): Response string.
        model (String): Model name string.

    Yields:
        str: JSON response chunks.
    """

    OpenAIResp = {
        "id": f"chatcmpl-{str(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "delta": {
                    "role": "assistant",
                    "content": message,
                },
                "index": 0,
                "finish_reason": "Stop",
            }
        ],
    }

    # openairesp = {
    # "id": f"chatcmpl-{str(time.time())}",
    # "object": "chat.completion.chunk",
    # "created": int(time.time()),
    # "model": "gpt-3.5-turbo",
    # "choices": [
    #     {
    #         "message": {
    #             "role": "assistant",
    #             "content": resp,
    #         },
    #         "index": 0,
    #         "finish_reason": "stop",
    #     }
    # ],

    jsonresp = json.dumps(OpenAIResp)

    yield f"{jsonresp}\n"



#############################################
####                                      ###
#####               Main                 ####
####                                     ####

if __name__ == "__main__":
    """Parse arguments and run the UVicorn server.

    This allows running the FastAPI server from the command line
    by specifying the host, port, and whether to enable auto-reloading.

    Example:
        python main.py --host 127.0.0.1 --port 8000 --reload

    """
    parser = argparse.ArgumentParser(description="Run the UVicorn server.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host IP address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()

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
