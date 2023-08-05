# Standard Library Imports
import argparse
import asyncio
import configparser
import json
import os
import sys
import time
import urllib.parse

# Third-Party Imports
import anyio
import browser_cookie3
import uvicorn
import requests
from aiohttp import request
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from h11 import Response
from pydantic import BaseModel

# Local Imports
from revChatGPT.V1 import Chatbot
from revChatGPT.typings import Error
from bard import ChatbotBard
from claude import Client
from anyio import Path


########################################
####                                ####
#####       Global Initilize       #####
####                                ####

"""Config file name and paths for chatbot API configuration."""
Free_Chatbot_API_CONFIG_FILE_NAME = "Config.conf"
Free_Chatbot_API_CONFIG_FOLDER = os.getcwd()

# CONFIG_FOLDER = os.path.expanduser("~/.config")
# Free_Chatbot_API_CONFIG_FOLDER = Path(CONFIG_FOLDER) / "Free_Chatbot_API"


FixConfigPath = lambda: (
    Path(Free_Chatbot_API_CONFIG_FOLDER) / Free_Chatbot_API_CONFIG_FILE_NAME
    if os.path.basename(Free_Chatbot_API_CONFIG_FOLDER).lower() == "src"
    else Path(Free_Chatbot_API_CONFIG_FOLDER)
    / "src"
    / Free_Chatbot_API_CONFIG_FILE_NAME
)

"""Path to API configuration file.""" 
Free_Chatbot_API_CONFIG_PATH = FixConfigPath()


"""FastAPI application instance."""
app = FastAPI()

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
    session_id: str = ""
    stream: bool = True

"""ChatGPT request message data model.""" 
class MessageChatGPT(BaseModel):
    messages: list[dict[str, str]]
    model: str = "gpt-3.5-turbo"
    temperature: float = 0.9
    top_p: float = 0.8
    session_id: str = ""
    stream: bool = True


########################################
####                                ####
#####           ChatGPT            #####
####                                ####


async def getGPTData(chat: Chatbot, message: Message):
    """Gets response data from ChatGPT API.

    Args:
        chat (Chatbot): Chatbot client object
        message (Message): Message request object

    Yields:
        str: ChatGPT response chunks
    
    Raises:
        ConnectionError: If internet connection or API server is unavailable
        HTTPError: If HTTP error response received from API
        RequestException: If other request error occurs
        Exception: For any other errors
    
    """
    try:
        prev_text = ""
        for data in chat.ask(message.message):
            msg = data["message"][len(prev_text) :]
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
                            "content": msg,
                        },
                        "index": 0,
                        "finish_reason": "[DONE]",
                    }
                ],
            }

            js = json.dumps(openai_response, indent=2)
            # print(js)

            prev_text = data["message"]

            try:
                yield (f"{msg}")
            except:
                continue

    except requests.exceptions.ConnectionError:
        # Handle the ConnectionError exception here
        print(
            "Connection error occurred. Please check your internet connection or the server's availability."
        )
        yield (
            "Connection error occurred. Please check your internet connection or the server's availability."
        )

    except requests.exceptions.HTTPError as http_err:
        # Handle HTTPError (e.g., 404, 500) if needed
        print(f"HTTP error occurred: {http_err}")
        yield (f"HTTP error occurred: {http_err}")

    except requests.exceptions.RequestException as req_err:
        # Handle other request exceptions if needed
        print(f"Request error occurred: {req_err}")
        yield (f"Request error occurred: {req_err}")

    except Exception as e:
        print(f"Error: {str(e)}")
        yield (str(f"Error: {str(e)}"))


@app.post("/chatgpt")
async def ask_gpt(request: Request, message: Message):
    """API endpoint to get response from ChatGPT.
    
    Args:
        request (Request): API request object.
        message (Message): Message request object.
        
    Returns:
        str: ChatGPT response.
    
    Raises:
        ConnectionError: If internet connection or API server is unavailable.
        HTTPError: If HTTP error response received from API.
        RequestException: If other request error occurs.
        Error: If ChatGPT API error occurs.
    
    """
    access_token = message.session_id
    # if not IsSession(access_token):
    #     access_token = os.getenv("OPENAI_API_SESSION")
    if not IsSession(access_token):
        config = configparser.ConfigParser()
        config.read(filenames=Free_Chatbot_API_CONFIG_PATH)
        access_token = config.get("ChatGPT", "ACCESS_TOKEN", fallback=None)
        if not IsSession(access_token):
            # answer = {f"answer": "You should set ACCESS_TOKEN in {Free_Chatbot_API_CONFIG_FILE_NAME} file or send it as an argument."}["answer"]
            answer = f"You should set ACCESS_TOKEN in {Free_Chatbot_API_CONFIG_FILE_NAME} file or send it as an argument."
            # print(answer)
            return answer

    chatbot = Chatbot(config={"access_token": access_token})

    response = []
    if message.stream == True:
        try:
            return StreamingResponse(
                getGPTData(chat=chatbot, message=message),
                media_type="text/event-stream",
            )

        # return "".join(response)
        # # return {"response": "".join(response)}

        except Exception as e:
            if isinstance(e, Error):
                try:
                    # err = e.message
                    # if e.__notes__:
                    #     err = f"{err} \n\n {e.__notes__}"
                    js = json.loads(e.message)
                    print(js["detail"]["message"])
                    return js["detail"]["message"]
                except:
                    print(e)
                    return e
            else:
                print(e)
                return e
    else:
        try:
            for data in chatbot.ask(message.message):
                response = data["message"]

            return response
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

        except Exception as e:
            if isinstance(e, Error):
                try:
                    # err = e.message
                    # if e.__notes__:
                    #     err = f"{err} \n\n {e.__notes__}"
                    js = json.loads(e.message)
                    print(js["detail"]["message"])
                    return js["detail"]["message"]
                except:
                    print("Error 01: ")
                return e
            else:
                print("Error 02: ")
                return e


########################################
####                                ####
#####          The Bard            #####
####                                ####


@app.post("/bard")
async def ask_bard(request: Request, message: Message):
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
    def CreateBardResponse(msg: str) -> json:
        if msg:
            answer = {"answer": msg}["answer"]
            return answer

    # Execute code without authenticating the resource
    session_id = message.session_id
    # if not IsSession(session_id):
    #     session_id = os.getenv("SESSION_ID")
    #     # print("Session: " + str(session_id) if session_id is not None else "Session ID is not available.")

    if not IsSession(session_id):
        config = configparser.ConfigParser()
        config.read(filenames=Free_Chatbot_API_CONFIG_PATH)
        session_id = config.get("Bard", "SESSION_ID", fallback=None)
        if not IsSession:
            answer = {
                f"answer": "You should set SESSION_ID in {Free_Chatbot_API_CONFIG_FILE_NAME} file for the Bard or send it as an argument."
            }["answer"]
            answer = CreateBardResponse(
                f"You should set SESSION_ID in {Free_Chatbot_API_CONFIG_FILE_NAME} file for the Bard or send it as an argument."
            )
            print(answer)
            return answer

    chatbot = ChatbotBard(session_id)

    if not message.message:
        message.message = "Hi, are you there?"

    if message.stream:
        try:
            # این شرط رو برای حالت غیر Stream نزاشتم چون در اون حالت خطای بهتری رو نشون میده اگر که اینترنت مشکل داشته باشه.
            if not chatbot.SNlM0e:
                return {"Error": "Check the Bard session."}

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
            # print(response["choices"][0]["message"]["content"][0])
            return response["choices"][0]["message"]["content"][0]
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


########################################
####                                ####
#####           Claude2            #####
####                                ####


@app.post("/claude")
async def ask_claude(request: Request, message: Message):
    """API endpoint to get Claude response.
    
    Args:
        request (Request): API request object.
        message (Message): Message request object.
        
    Returns:
        str: JSON string of Claude response.
    
    """
    cookie = message.session_id

    # if not cookie:
    #     cookie = os.environ.get("CLAUDE_COOKIE")

    if not cookie:
        cookie = get_claude_cookie()
        if not cookie:
            config = configparser.ConfigParser()
            config.read(filenames=Free_Chatbot_API_CONFIG_PATH)
            cookie = config.get("Claude", "COOKIE", fallback=None)
            if not cookie:
                response_error = {
                    f"Error": f"You should set 'COOKIE' in '{Free_Chatbot_API_CONFIG_FILE_NAME}' file for the Bard or send it as an argument."
                }

                print(response_error)
                return response_error
                # raise ValueError(
                #     f"You should set 'COOKIE' in '{Free_Chatbot_API_CONFIG_FILE_NAME}' file for the Bard or send it as an argument."
                # )

        cookie = f"sessionKey={cookie}"

    claude = Client(cookie)
    conversation_id = None

    if not conversation_id:
        conversation = claude.create_new_chat()
        conversation_id = conversation["uuid"]

    if not message.message:
        message.message = "Hi, are you there?"

    if message.stream:
        return StreamingResponse(
            claude.stream_message(message.message, conversation_id),
            media_type="text/event-stream",
        )
    else:
        response = claude.send_message(message.message, conversation_id)
        # print(response)
        return response


##########################################
####                                  ####
######     ChatGPT Endpoint         ######
####    `/v1/chat/completions`       #####


async def getChatGPTData(chat: Chatbot, message: MessageChatGPT):
    """Gets AI response data from ChatGPT Website.

    Args:
        chat (Chatbot): Chatbot client object.
        message (MessageChatGPT): Message request object.

    Yields: 
        str: JSON response chunks.
    """
    try:
        prev_text = ""
        for data in chat.ask(str(message.messages[0])):
            # remove b' and ' at the beginning and end and ignore case
            # line = str(data)[2:-1]
            line = str(data)
            if not line or line is None:
                continue
            if "data: " in line:
                line = line[6:]
            if line == "[DONE]":
                break

            # DO NOT REMOVE THIS
            # line = line.replace('\\"', '"')
            # line = line.replace("\\'", "'")
            # line = line.replace("\\\'", "\\")

            try:
                # https://stackoverflow.com/questions/4162642/single-vs-double-quotes-in-json/4162651#4162651
                # import ast
                # line = ast.literal_eval(line)
                line = eval(line)
                line = json.loads(json.dumps(line))

            # except json.decoder.JSONDecodeError as e:
            except Exception as e:
                print(f"ERROR Decode: {e}")
                continue

            # if line.get("message").get("author").get("role") != "assistant":
            if line.get("author").get("role") != "assistant":
                continue

            cid = line["conversation_id"]
            pid = line["parent_id"]

            author = {}
            author = line.get("author", {})

            message = line["message"]

            model = line["model"]
            finish_details = line["finish_details"]

            res_text = message[len(prev_text) :]
            prev_text = message

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

            shellresp = {
                "id": f"chatcmpl-{str(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "temperature": 0.1,
                "top_probability": 1.0,
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

            jsonresp = json.dumps(shellresp)

            yield f"{jsonresp}\n"

    except Exception as e:
        print(f"Error : {str(e)}")
        yield f"Error : {str(e)}"


@app.post("/v1/chat/completions")
def ask_chatgpt(request: Request, message: MessageChatGPT):
    """API endpoint to get ChatGPT response.

    Args:
        request (Request): API request object.
        message (MessageChatGPT): Message request object.
    
    Returns:
        str: ChatGPT response.
    """
    access_token = os.getenv("OPENAI_API_SESSION")
    if not IsSession(access_token):
        config = configparser.ConfigParser()
        config.read(filenames=Free_Chatbot_API_CONFIG_PATH)
        access_token = config.get("ChatGPT", "ACCESS_TOKEN", fallback=None)
        if not IsSession(access_token):
            # answer = {f"answer": "You should set ACCESS_TOKEN in {Free_Chatbot_API_CONFIG_FILE_NAME} file or send it as an argument."}["answer"]
            answer = f"You should set ACCESS_TOKEN in {Free_Chatbot_API_CONFIG_FILE_NAME} file or send it as an argument."
            # print(answer)
            return answer

    chatbot = Chatbot(
        config={
            "access_token": access_token,
        }
    )

    response = []
    if message.stream == True:
        try:
            return StreamingResponse(
                getChatGPTData(chat=chatbot, message=message),
                media_type="application/json",
            )

        # return "".join(response)
        # # return {"response": "".join(response)}

        except Exception as e:
            if isinstance(e, Error):
                try:
                    # err = e.message
                    # if e.__notes__:
                    #     err = f"{err} \n\n {e.__notes__}"
                    js = json.loads(e.message)
                    print(js["detail"]["message"])
                    return js["detail"]["message"]
                except:
                    print(e)
                    return e
            else:
                print(e)
                return e
    else:
        try:
            # print(" # Normal Request #")
            for data in chatbot.ask(message.message):
                response = data["message"]
            return response
            # print(response)
        except Exception as e:
            if isinstance(e, Error):
                try:
                    # err = e.message
                    # if e.__notes__:
                    #     err = f"{err} \n\n {e.__notes__}"
                    js = json.loads(e.message)
                    print(js["detail"]["message"])
                    return js["detail"]["message"]
                except:
                    print(e)
                    return e
            else:
                print(list(e))
                return e


########################################
####                                ####
#####     Develope Functions       #####
####                                ####

# print("".join(response))


@app.post("/DevMode")
async def ask_debug(request: Request, message: Message) -> dict:
    """API endpoint to get response in developer mode.

    This endpoint allows executing code without authentication 
    if the correct authorization key is provided in the headers.

    Args:
        request (Request): API request object
        message (Message): Message request object

    Returns:
        dict: Chatbot response

    Raises:
        HTTPException: If invalid authorization key is provided
    
    """

    # Get the user-defined auth key from the environment variables
    user_auth_key = os.getenv("USER_AUTH_KEY")

    # Check if the user has defined an auth key,
    # If so, check if the auth key in the header matches it.
    if user_auth_key and user_auth_key != request.headers.get("Authorization"):
        raise HTTPException(status_code=401, detail="Invalid authorization key")

    # Execute your code without authenticating the resource
    chatbot = Chatbot(message.session_id)
    response = chatbot.ask(message.message)

    # print(response['choices'][0]['content'][0])
    return response


def fake_data_streamer_OLD():
    for i in range(10):
        yield b"some fake data\n"
        time.sleep(0.5)


def fake_data_streamer():
    openai_response = {
        "id": f"chatcmpl-{str(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "gpt-3.5-turbo-0613",
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
    for i in range(10):
        yield f"{openai_response}\n"
        # yield b"some fake data\n"
        time.sleep(0.5)


########################################
####                                ####
#####        Other Functions       #####
####                                ####


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
    if not session_id:
        return False
    if session_id.lower() == "none":
        return False

    return True


def get_claude_cookie(filter_text="sessionKey") -> str:
    """
    Retrieve the value of a specific cookie from the 'claude' domain, filtered by cookie name.

    This function retrieves the value of a specific cookie from the 'claude' domain by filtering
    cookies based on the provided `filter_text`. The filtering is case-insensitive. It returns
    the value of the last matching cookie found, or `None` if no matching cookies are found.

    Parameters:
    filter_text (str, optional): The text used to filter cookies based on their name. The default
                                 value is "sessionKey".

    Returns:
    str or None: The value of the last matching cookie, or `None` if no matching cookies are found.
    """

    domain = "claude"
    cookies = browser_cookie3.load(domain)

    filtered_cookies = [
        cookie for cookie in cookies if filter_text.lower() in cookie.name.lower()
    ]

    result = None
    if filtered_cookies:
        result = filtered_cookies[-1].value

    return result


########################################
####                                ####
#####            Main              #####
####                                ####

if __name__ == "__main__":
    """Parse arguments and run the UVicorn server.

    This allows running the FastAPI server from the command line
    by specifying the host, port, and whether to enable auto-reloading.

    Example:
        python main.py --host 0.0.0.0 --port 8000 --reload

    """

    parser = argparse.ArgumentParser(description="Run the UVicorn server.")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host IP address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload
    )

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
