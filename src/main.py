import os
import requests
import configparser
import urllib.parse
import json
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bard import Chatbot
from claude import Client

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


@app.post("/ask")
async def ask(request: Request, message: Message) -> dict:
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


@app.post("/bard")
async def ask(request: Request, message: Message):
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
        config.read("Config.conf")
        session_id = config.get("Bard", "SESSION_ID", fallback=None)
        if not IsSession:
            # answer = {"answer": "You should set SESSION_ID in Config.conf file or send it as an argument."}["answer"]
            answer = CreateBardResponse(
                "You should set SESSION_ID in Config.conf file or send it as an argument."
            )
            print(answer)
            return answer

    chatbot = Chatbot(session_id)

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

            """
            result = {}
            result["choices"] = [{}]
            result["choices"][0]["message"] = {}
            result["choices"][0]["message"]["content"] = []
            result["choices"][0]["message"]["content"].append("Test")
            
            json_data = {
                "choices": [{"message": {"content": results["choices"][0]["content"]}}]
            }
            """


@app.post("/claude")
async def ask(request: Request, message: Message):
    cookie = os.environ.get("CLAUDE_COOKIE")
    if not cookie:
        config = configparser.ConfigParser()
        config.read("Config.conf")
        cookie = config.get("Claude", "COOKIE", fallback=None)

    if not cookie:
        raise ValueError("Please set the 'cookie' environment variable.")

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


def IsSession(session_id: str) -> bool:
    # if session_id is None or not session_id or session_id.lower() == "none":
    if session_id is None:
        return False
    if not session_id:
        return False
    if session_id.lower() == "none":
        return False

    return True
