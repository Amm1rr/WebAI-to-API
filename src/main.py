#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import os
from anyio import Path
import uvicorn
from aiohttp import request
from h11 import Response
import requests
import configparser
import urllib.parse
import json
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bard import ChatbotBard
from claude import Client
import itertools
from revChatGPT.V1 import Chatbot
from revChatGPT.typings import Error


########################################
####                                ####
#####       Global Initilize       #####
####                                ####

Free_Chatbot_API_CONFIG_FILE_NAME = "Config.conf"
Free_Chatbot_API_CONFIG_FOLDER = os.getcwd()

# CONFIG_FOLDER = os.path.expanduser("~/.config")
# Free_Chatbot_API_CONFIG_FOLDER = Path(CONFIG_FOLDER) / "Free_Chatbot_API"


FixConfigPath = lambda: (
    Path(Free_Chatbot_API_CONFIG_FOLDER)
    / Free_Chatbot_API_CONFIG_FILE_NAME
    if os.path.basename(Free_Chatbot_API_CONFIG_FOLDER).lower() == "src"
    else Path(Free_Chatbot_API_CONFIG_FOLDER) / "src" / Free_Chatbot_API_CONFIG_FILE_NAME
)

Free_Chatbot_API_CONFIG_PATH = FixConfigPath()


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Message(BaseModel):
    message: str = ""
    session_id: str = ""
    stream: bool = True


class MessageChatGPT(BaseModel):
    messages: list[dict[str, str]]
    model: str = "gpt-3.5-turbo-0613"
    temperature: float = 0.9
    top_p: float = 0.8
    session_id: str = ""
    stream: bool = True


########################################
####                                ####
#####           ChatGPT            #####
####                                ####


async def getGPTData(chat: Chatbot, message: Message):
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
                yield(f"{msg}")
            except:
                continue
    
    except requests.exceptions.ConnectionError:
        # Handle the ConnectionError exception here
        print("Connection error occurred. Please check your internet connection or the server's availability.")
        yield("Connection error occurred. Please check your internet connection or the server's availability.")

    except requests.exceptions.HTTPError as http_err:
        # Handle HTTPError (e.g., 404, 500) if needed
        print(f"HTTP error occurred: {http_err}")
        yield(f"HTTP error occurred: {http_err}")

    except requests.exceptions.RequestException as req_err:
        # Handle other request exceptions if needed
        print(f"Request error occurred: {req_err}")
        yield(f"Request error occurred: {req_err}")
    
    except Exception as e:
        print(f"Error: {str(e)}")
        yield(str(f"Error: {str(e)}"))



@app.post("/chatgpt")
async def ask_gpt(request: Request, message: Message):
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
            print("Connection error occurred. Please check your internet connection or the server's availability.")
            return("Connection error occurred. Please check your internet connection or the server's availability.")

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

    if not chatbot.SNlM0e:
        return {"Error": "Check the Bard session."}

    if not message.message:
        message.message = "Hi, are you there?"

    if message.stream:
        return StreamingResponse(
            chatbot.ask_bardStream(message.message),
            media_type="text/event-stream",
        )
    else:
        try:
            response = chatbot.ask_bard(message.message)
            # print(response["choices"][0]["message"]["content"][0])
            return response["choices"][0]["message"]["content"][0]
        except:
            try:
                return response["content"]
            except:
                return response


########################################
####                                ####
#####           Claude2            #####
####                                ####


@app.post("/claude")
async def ask_claude(request: Request, message: Message):
    cookie = message.session_id

    # if not cookie:
    #     cookie = os.environ.get("CLAUDE_COOKIE")

    if not cookie:
        config = configparser.ConfigParser()
        config.read(filenames=Free_Chatbot_API_CONFIG_PATH)
        cookie = config.get("Claude", "COOKIE", fallback=None)
        if not cookie:
            answer = {
                f"Error": f"You should set 'COOKIE' in '{Free_Chatbot_API_CONFIG_FILE_NAME}' file for the Bard or send it as an argument."
            }

            print(answer)
            return answer
            # raise ValueError(
            #     f"You should set 'COOKIE' in '{Free_Chatbot_API_CONFIG_FILE_NAME}' file for the Bard or send it as an argument."
            # )

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
    try:
        prev_text = ""
        for data in chat.ask(str(message.messages)):
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
# print(message, end="", flush=True) #این خط باعث میشه توی ترمینال به خط بعدی نره


@app.post("/DevMode")
async def ask_debug(request: Request, message: Message) -> dict:
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
    # if session_id is None or not session_id or session_id.lower() == "none":
    if session_id is None:
        return False
    if not session_id:
        return False
    if session_id.lower() == "none":
        return False

    return True


########################################
####                                ####
#####            Main              #####
####                                ####

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )

    ##### TO USE HTTPS
    ###
    # from subprocess import Popen
    # Popen(["python", "-m", "https_redirect"])  # Add this
    # uvicorn.run(
    #     "main:app",
    #     port=443,
    #     host="0.0.0.0",
    #     reload=True,
    #     reload_dirs=["html_files"],
    #     ssl_keyfile="/etc/letsencrypt/live/my_domain/privkey.pem",
    #     ssl_certfile="/etc/letsencrypt/live/my_domain/fullchain.pem",
    # )
