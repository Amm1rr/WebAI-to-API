import os
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from Bard import Chatbot
from claude_api import Client
import configparser
import urllib.parse
import json

app = FastAPI()

''' Add Origin

origins = [
    "http://localhost:Port"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
'''

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
        raise HTTPException(status_code=401, detail='Invalid authorization key')

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
        raise HTTPException(status_code=401, detail='Invalid authorization key')

    # Execute your code without authenticating the resource
    # chatbot = Chatbot(message.session_id)
    chatbot = Chatbot(os.getenv("SESSION_ID"))
    response = chatbot.ask_bard(message.message)
    # print(response)

    # print(response["choices"][0]["message"]["content"])
    return response

@app.post("/claude")
async def ask(request: Request, message: Message) -> str:

    cookie = os.environ.get('CLAUDE_COOKIE')
    if not cookie:
        config = configparser.ConfigParser()
        config.read("Config.conf")
        cookie = config.get('CLAUDE', 'CLAUDE_COOKIE', fallback=None)

    if not cookie:
        raise ValueError("Please set the 'cookie' environment variable.")
    
    claude = Client(cookie)
    conversation_id = None

    if not conversation_id:
        conversation = claude.create_new_chat()
        conversation_id = conversation['uuid']

    response = claude.send_message(message.message, conversation_id)
    # print(response)
    return response
