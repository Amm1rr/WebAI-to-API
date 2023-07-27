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


# print("".join(response))
# print(message, end="", flush=True) #این خط باعث میشه توی ترمینال به خط بعدی نره

CONFIG_FOLDER = os.path.expanduser("~/.config")
Free_Chatbot_API_CONFIG_FILE_NAME = "Config.conf"
Free_Chatbot_API_CONFIG_FOLDER = Path(CONFIG_FOLDER) / "Free_Chatbot_API"
Free_Chatbot_API_CONFIG_PATH = Path(Free_Chatbot_API_CONFIG_FOLDER) / Free_Chatbot_API_CONFIG_FILE_NAME

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def fake_data_streamer():
    for i in range(10):
        yield b"some fake data\n\n"
        time.sleep(0.5)


class Message(BaseModel):
    session_id: str
    message: str
    stream: bool

async def getChatGPTData(chat: Chatbot, message: Message):
    # try:
        # response = []
        prev_text = ""
        for data in chat.ask(message.message):
            msg = data["message"][len(prev_text) :]
            # response.append(msg)
            prev_text = data["message"]
            # yield "".join(msg)
            # yield "data:" + str(msg) + "\n\n"
            yield str(msg) + "\n\n"
            print(str(msg))
            # time.sleep(0.1)
            # await asyncio.sleep(0.1)
            # return {"response": "".join(response)}

    # except Exception as e:
    #     if isinstance(e, Error):
    #         try:
    #             # err = e.message
    #             # if e.__notes__:
    #             #     err = f"{err} \n\n {e.__notes__}"
    #             js = json.loads(e.message)
    #             print(js["detail"]["message"])
    #             yield js["detail"]["message"]
    #         except:
    #             print(e)
    #             yield e
    #     else:
    #         print(e)
    #         yield e


@app.post("/chatgpt")
# @app.post("/V1/chat/completions")
async def ask_chatgpt(request: Request, message: Message):
    access_token = message.session_id
    if not IsSession(access_token):
        access_token = os.getenv("OPENAI_API_SESSION")
    if not IsSession(access_token):
        config = configparser.ConfigParser()
        config.read(filenames=Free_Chatbot_API_CONFIG_PATH)
        access_token = config.get("ChatGPT", "ACCESS_TOKEN", fallback=None)
        if not IsSession(access_token):
            # answer = {f"answer": "You should set ACCESS_TOKEN in {Free_Chatbot_API_CONFIG_FILE_NAME} file or send it as an argument."}["answer"]
            answer = f"You should set ACCESS_TOKEN in {Free_Chatbot_API_CONFIG_FILE_NAME} file or send it as an argument."
            print(answer)
            return answer

    chatbot = Chatbot(config={"access_token": access_token})

    response = []
    prev_text = ""
    if message.stream == True:
        try:
            return StreamingResponse(
                getChatGPTData(chat=chatbot, message=message),
                media_type="text/event-stream",
            )
            # return StreamingResponse(
            #     fake_data_streamer(),
            #     media_type="text/event-stream",
            # )  # application/json

            # for data in chatbot.ask(message.message):
            #     message = data["message"][len(prev_text) :]
            #     response.append(message)
            #     prev_text = data["message"]

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


@app.post("/bard")
async def ask_bard(request: Request, message: Message):
    def CreateBardResponse(msg: str) -> json:
        if msg:
            answer = {"answer": msg}["answer"]
            return answer

    def CreateShellResponse(msg: str) -> json:
        if msg:
            answer = {"answer": msg, "choices": [{"message": {"content": msg}}]}
            return answer

    # Execute code without authenticating the resource
    session_id = message.session_id
    if not IsSession(session_id):
        session_id = os.getenv("SESSION_ID")
        # print("Session: " + str(session_id) if session_id is not None else "Session ID is not available.")

    if not IsSession(session_id):
        config = configparser.ConfigParser()
        config.read(filenames=Free_Chatbot_API_CONFIG_PATH)
        session_id = config.get("Bard", "SESSION_ID", fallback=None)
        if not IsSession:
            answer = {f"a/nswer": "You should set SESSION_ID in {Free_Chatbot_API_CONFIG_FILE_NAME} file or send it as an argument."}["answer"]
            answer = CreateBardResponse(
                f"You should set SESSION_ID in {Free_Chatbot_API_CONFIG_FILE_NAME} file or send it as an argument."
            )
            print(answer)
            return answer

    chatbot = ChatbotBard(session_id)

    if not message.message:
        message.message = "Hi, are you there?"

    if message.stream:
        return StreamingResponse(
            chatbot.ask_bardStream(message.message),
            media_type="text/event-stream",
        )  # application/json
    else:
        response = chatbot.ask_bard(message.message)
        try:
            # print(response["choices"][0]["message"]["content"][0])
            return response["choices"][0]["message"]["content"][0]
            # answer = CreateBardResponse(response["choices"][0]["message"]["content"][0])
            # print(answer)
            # return answer
        except:
            try:
                return response["content"]
            except:
                return response


@app.post("/claude")    
async def ask_claude(request: Request, message: Message):
    cookie = os.environ.get("CLAUDE_COOKIE")
    if not cookie:
        config = configparser.ConfigParser()
        config.read(filenames=Free_Chatbot_API_CONFIG_PATH)
        cookie = config.get("Claude", "COOKIE", fallback=None)
        print(cookie)

    if not cookie:
        raise ValueError(f"Please set the 'COOKIE' for Claude in confing file:\n\n {Free_Chatbot_API_CONFIG_PATH}")

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
        )  # application/json
    else:
        response = claude.send_message(message.message, conversation_id)
        # print(response)
        return response

    # return StreamingResponse(fake_data_streamer(), media_type='text/event-stream')

    # or, use:
    # headers = {'X-Content-Type-Options': 'nosniff'}
    # return StreamingResponse(fake_data_streamer(), headers=headers, media_type='text/plain')

    # response = claude.send_message(message.message, conversation_id)
    # async def event_stream():
    #     i=0
    #     while i < 1:
    #         i=i+1
    #     # while True:
    #         # response = claude.send_message(message.message, conversation_id)
    #         response = claude.stream_message(message.message, conversation_id)
    #         print(list(response))
    #         # yield f"data: {json.dumps(response)}\n\n"
    #         yield f"data: {list(response)}\n\n"
    #         time.sleep(0.10)  # Adjust this time interval as needed
    # return StreamingResponse(event_stream(), media_type="text/event-stream")

    # print(response)
    # return response


@app.post("/ask_debug")
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


def IsSession(session_id: str) -> bool:
    # if session_id is None or not session_id or session_id.lower() == "none":
    if session_id is None:
        return False
    if not session_id:
        return False
    if session_id.lower() == "none":
        return False

    return True


if __name__ == "__main__":
    print("Run")
    uvicorn.run(app, host="0.0.0.0", port=8000)
