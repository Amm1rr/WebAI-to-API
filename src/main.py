import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import requests
from bard import Chatbot
from claude_api import Client
import configparser
import urllib.parse
import json
import time
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

''' Add Origin
origins = [
    "http://localhost:Port"
]
'''

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def fake_data_streamer():
    for i in range(10):
        yield b'some fake data\n\n'
        time.sleep(0.5)


class Message(BaseModel):
    session_id: str
    message: str


@app.post("/ask")
async def ask(request: Request, message: Message) -> dict:
    # Get the user-defined auth key from the environment variables
    user_auth_key = os.getenv('USER_AUTH_KEY')

    # Check if the user has defined an auth key,
    # If so, check if the auth key in the header matches it.
    if user_auth_key and user_auth_key != request.headers.get('Authorization'):
        raise HTTPException(
            status_code=401, detail='Invalid authorization key')

    # Execute your code without authenticating the resource
    chatbot = Chatbot(message.session_id)
    response = chatbot.ask(message.message)

    # print(response['choices'][0]['content'][0])
    return response


@app.post("/bard")
async def ask(request: Request, message: Message) -> dict:
    # Get the user-defined auth key from the environment variables
    user_auth_key = os.getenv('USER_AUTH_KEY')

    # Check if the user has defined an auth key,
    # If so, check if the auth key in the header matches it.
    if user_auth_key and user_auth_key != request.headers.get('Authorization'):
        raise HTTPException(
            status_code=401, detail='Invalid authorization key')

    # Execute your code without authenticating the resource
    # chatbot = Chatbot(message.session_id)
    chatbot = Chatbot(os.getenv("SESSION_ID"))
    response = chatbot.ask_bard(message.message)
    # print(response)

    # print(response["choices"][0]["message"]["content"])
    return response


@app.post("/claude")
async def ask(request: Request, message: Message):

    cookie = os.environ.get('CLAUDE_COOKIE')
    if not cookie:
        config = configparser.ConfigParser()
        config.read("Config.conf")
        cookie = config.get('Claude', 'COOKIE', fallback=None)

    if not cookie:
        raise ValueError("Please set the 'cookie' environment variable.")

    claude = Client(cookie)
    conversation_id = None

    if not conversation_id:
        conversation = claude.create_new_chat()
        conversation_id = conversation['uuid']

    # return StreamingResponse(fake_data_streamer(), media_type='text/event-stream')
    
    if not message.message:
        message.message = "Hi,"
    return StreamingResponse(claude.stream_message(message.message, conversation_id), media_type='text/event-stream')
    # return StreamingResponse(claude.send_message(message.message, conversation_id), media_type='text/event-stream')
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
