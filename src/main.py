# Standard Library Imports
import argparse
import configparser
import json
import os
import sys
import time
from typing import Literal
import urllib.parse

# Third-Party Imports
import browser_cookie3
import uvicorn

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from h11 import Response
from pydantic import BaseModel

# Local Imports
from bard import ChatbotBard
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


"""ChatGPT request message data model."""


class MessageChatGPT(BaseModel):
    messages: list[dict[str, str]]
    model: str = "gpt-3.5-turbo"
    temperature: float = 0.9
    top_p: float = 0.8
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
    # if not IsSession(session_id):
    #     session_id = os.getenv("SESSION_ID")
    #     # print("Session: " + str(session_id) if session_id is not None else "Session ID is not available.")
    cookies = None
    def get_session_id_Bard(sessionId: str = "SESSION_ID"):
        """Get the session ID for Bard.

        Args:
            sessionId (str, optional): The session ID to get. Defaults to "SESSION_ID".

        Returns:
            str: The session ID.
        """
        try:
            config = configparser.ConfigParser()
            config.read(CONFIG_FILE_PATH)
            sess_id = config.get("Germini", sessionId)

        except Exception as e:
            # print(e)
            sess_id = None
        
        if not sess_id:
            sessions = get_cookies(".google.com")
            return sessions
        else:
            session_name = "Bard" if sessionId == "SESSION_ID" else ("BardTS" if sessionId == "SESSION_DTS" else "BardCC")
            sess_id = get_Cookie(session_name)
              
            if not IsSession(sess_id):
              print(f"You should set {sessionId} for Bard in {CONFIG_FILE_NAME}")
  
            return sess_id

    #if not session_id:
    #    session_id = get_session_id_Bard("SESSION_ID")

    #if not session_idTS:
    #    session_idTS = get_session_id_Bard("SESSION_IDTS")
    
    #if not session_idCC:
    #   session_idCC = get_session_id_Bard("SESSION_IDCC")
    chatbot = None
    if not (session_id or session_idTS or session_idCC):
      cookies = get_session_id_Bard()
      if type(cookies) == dict:
        chatbot = ChatbotBard(cookies)
      else:
        chatbot = ChatbotBard(session_id=session_id, session_idTS=session_idTS, session_idCC=session_idCC)
        
    else:
      chatbot = ChatbotBard(session_id=session_id, session_idTS=session_idTS, session_idCC=session_idCC)
    
    if not message.message:
        message.message = "Hi, are you there?"

    if message.stream:
        try:
            # این شرط رو برای حالت غیر Stream نزاشتم چون در اون حالت خطای بهتری رو نشون میده اگر که اینترنت مشکل داشته باشه.
            # if not chatbot.SNlM0e:
            #     return {"Error": "Check the Bard session."}

            return StreamingResponse(
                chatbot.ask_bardStream(message.message),
                media_type="text/event-stream",
            )
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
            response = chatbot.ask_bard(message.message)
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
            cookie = get_Cookie("Claude")
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

    if not conversation_id:
        conversation = claude.create_new_chat()
        conversation_id = conversation["uuid"]

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


async def getChatGPTData(message: MessageChatGPT):
    """Gets AI response data from ChatGPT Website.

    Args:
        chat (Chatbot): Chatbot client object.
        message (MessageChatGPT): Message request object.

    Yields:
        str: JSON response chunks.
    """
    jsonresp = {
        "author": author,
        "message": res_text,
        "conversation_id": cid,
        "parent_id": pid,
        "model": model,
        "finish_details": finish_details,
        "end_turn": line["end_turn"],
        "recipient": line["recipient"],
        "citations": line["citations"],
    }

    OpenAIResp = {
        "id": f"chatcmpl-{str(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "delta": {
                    "role": "assistant",
                    "content": res_text,
                },
                "index": 0,
                "finish_reason": finish_details,
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
####                                     ####
#####        Develope Functions         #####
####                                     ####

# print("".join(response))


def fake_data_streamer_OLD():
    for _ in range(10):
        yield b"some fake data\n"
        time.sleep(0.5)


def fake_data_streamer():
    openai_response = {
        "id": f"chatcmpl-{str(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "gpt-3.5-turbo",
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 100,
            "total_tokens": 100,
        },
        "choices": [
            {
                "delta": {
                    "role": "assistant",
                    "content": "Yes",
                },
                "index": 0,
                "finish_reason": "[DONE]",
            }
        ],
    }
    for _ in range(10):
        yield f"{openai_response}\n"
        # yield b"some fake data\n"
        time.sleep(0.5)


#############################################
####                                     ####
#####          Other Functions          #####
####                                     ####


def IsSession(session_id: str) -> bool:
    """Checks if a valid session ID is provided.

    Args:
        session_id (str): The session ID to check

    Returns:
        bool: True if session ID is valid, False otherwise
    """

    # if session_id is None or not session_id or session_id.lower() == "none":
    if session_id is None:
        return False
    return False if not session_id else session_id.lower() != "none"


_cookies = {}
def get_cookies(cookie_domain: str) -> dict: 
     if cookie_domain not in _cookies: 
         _cookies[cookie_domain] = {} 
         for cookie in browser_cookie3.load(cookie_domain): 
             _cookies[cookie_domain][cookie.name] = cookie.value 
     return _cookies[cookie_domain]

def get_Cookie(service_Name: Literal["Bard", "BardTS", "BardCC", "Claude"]) -> str:
    """
    Retrieve and return the session cookie value for the specified service.

    This function takes a service name as input, either 'Bard', 'BardTS', or 'Claude',
    and retrieves the corresponding session cookie value from the browser's stored cookies.
    The cookie value is then returned.

    Note: This function requires the 'browser_cookie3' library to be installed.

    Args:
        service_name (Literal["Bard", "BardTS", "Claude"]): The name of the service
            for which to retrieve the session cookie.

    Returns:
        str: The session cookie value for the specified service, or None if no matching
            cookie is found.
    """

    domains = {
        "Bard": "google",
        "BardTS": "google",
        "BardCC": "google",
        "Claude": "claude",
    }
    domain = domains[service_Name]

    if service_Name.lower() == "bardts":
        bardSessionName = "__Secure-1PSIDTS"
    elif service_Name.lower() == "bardcc":
        bardSessionName = "__Secure-1PSIDCC"
    else:
        bardSessionName = "__Secure-1PSID"

    sessName = {
        "claude": "sessionKey",
        "google": bardSessionName,
    }
    sessionName = sessName[domain]

    cookies = browser_cookie3.load(domain_name=domain)

    return (
        filtered_cookies[-1].value
        if (
            filtered_cookies := [
                cookie
                for cookie in cookies
                if sessionName == cookie.name
            ]
        )
        else None
    )


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
