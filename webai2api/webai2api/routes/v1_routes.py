from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from ..models.claude import Client
from ..utils import utility
from gemini_webapi import GeminiClient
import logging
import copy
import asyncio
import os
import json

utility.configure_logging()
logging.info("v1_routes.py")

router = APIRouter()

# Global variables
COOKIE_CLAUDE = None
COOKIE_GEMINI = None
GEMINI_CLIENT = None
CLAUDE_CLIENT = None

# Constants
CONFIG_FILE_NAME = "Config.conf"
CONFIG_FOLDER = os.getcwd()
if "/webai2api" not in CONFIG_FOLDER:
    CONFIG_FOLDER += "/webai2api"
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, CONFIG_FILE_NAME)


# Initialize AI models and cookies
async def initialize_ai_models(config_file_path: str):
    global COOKIE_GEMINI, GEMINI_CLIENT, COOKIE_CLAUDE, CLAUDE_CLIENT

    COOKIE_GEMINI = utility.getCookie_Gemini()
    GEMINI_CLIENT = GeminiClient()

    COOKIE_CLAUDE = utility.getCookie_Claude(configfilepath=os.getcwd(), configfilename=config_file_path)
    CLAUDE_CLIENT = Client(COOKIE_CLAUDE)

    try:
        await GEMINI_CLIENT.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True, verbose=False)
    except Exception as e:
        print("initialize_ai_models Error: ", e)


# Startup event handler
async def startup():
    global CONFIG_FOLDER
    await initialize_ai_models(CONFIG_FOLDER)


router.add_event_handler("startup", startup)


@router.post("/v1/chat/completions")
async def ask_ai(message: dict):
    """API endpoint to get ChatGPT JSON response.

    Args:
        message (Message): Message request object.

    Returns:
        str: JSON string of ChatGPT JSON response.
    
    WebUI Configuration:
        Open http://localhost:8000/WebAI to configuration

    """

    if 'messages' not in message:
        print("ERROR: 'messages' key is missing in the message dictionary")
        # yield "ERROR: 'messages' key is missing in the message dictionary"
        # raise ValueError("'messages' key is missing in the message dictionary")
        logging.info("v1_routes:ERROR: 'messages' key is missing in the message dictionary")
        return "ERROR: 'messages' key is missing in the message dictionary"

    messages = message.get('messages', [])

    # Check if 'messages' is empty
    if not messages:
        print("'messages' key is empty")
        logging.info("v1_routes:'messages' key is empty")
        return "'messages' key is empty"
        # raise ValueError("'messages' key is empty")

    user_message_content = None
    conversation_id = None
    original_conversation_id = None
    stream = False
    model = None

    if type(messages) is str:
        print("messages: ", messages)
        return "Error in arguments."
    else:
        for msg in messages:
            # logging.info("messages: ", messages)
            # logging.info("msg: ", msg)
            if msg['role'] == "user":
                user_message_content = msg['content']

            elif 'conversation_id' in msg:

                # conversation_id = message.get('conversation_id')
                if message['conversation_id']:
                    message['conversation_id'] = None
                    conversation_id = None

                elif conversation_id == "string":
                    message['conversation_id'] = None
                    conversation_id = None

            elif 'stream' in msg and msg['stream']:
                stream = message.get('stream', False)

            elif 'stream' in msg and msg['model']:
                model = message.get('model', "gpt-3.5-turbo")

    if user_message_content is None:
        user_message_content = message.get('message')

    if conversation_id is not None:
        original_conversation_id = copy.deepcopy(conversation_id)

    if not user_message_content:
        # yield("Warning : Prompt is empty")
        print("Warning : Prompt is empty")
        return "Warning : Prompt is empty"

    open_ai_response_model = utility.ResponseModel(CONFIG_FILE_PATH)

    # logging.info("Model:", model)
    # logging.info("Prompt:", user_message_content)
    # logging.info("Stream:", stream)
    # logging.info("Conversation ID:", conversation_id)
    # logging.info("open_ai_response_model: ", open_ai_response_model)

    prompt = user_message_content

    if open_ai_response_model == "Gemini":

        logging.info("GEMINI")

        if not GEMINI_CLIENT:
            print(
                "warning: Looks like you're not logged in to Gemini. Please either set the Gemini cookie manually or "
                "log in to your gemini.google.com account through your web browser.")
            return

        try:
            response = await GEMINI_CLIENT.generate_content(prompt=prompt)
            response_return = utility.ConvertToChatGPT(message=response, model=open_ai_response_model)
            # logging.info("Converted to Gemini to ChatGPT: ",response_return)
            # yield json.dumps(response_return)
            return json.dumps(response_return)

        except Exception as req_err:
            print(f"Gemini Error Occurred: {req_err}")
            # raise
            return

    else:
        logging.info("CLAUDE")

        max_retry = 3
        current_retry = 0
        while current_retry < max_retry:
            try:
                if not conversation_id:
                    try:
                        conversation = CLAUDE_CLIENT.create_new_chat()
                        conversation_id = conversation["uuid"]
                        break
                    except Exception as e:
                        current_retry += 1
                        if current_retry == max_retry:
                            print("Warning Claude: Failed to create new chat.")
                            return "error: ", e
                        else:
                            print("Claude: Retrying in 1 second to create new chat...")
                            await asyncio.sleep(1)
                else:
                    break
            except Exception as e:
                print("Warning Claude: Failed to create new chat.")
                return "error: ", e

        if not original_conversation_id:
            # after the creation, you need to wait some time before to sendGemini
            await asyncio.sleep(2)

        if stream:
            response = CLAUDE_CLIENT.stream_message(prompt, conversation_id)
            # print(response)
            return StreamingResponse(
                response,
                media_type="text/event-stream",
            )
            await asyncio.sleep(0)
        else:
            response = CLAUDE_CLIENT.send_message(prompt, conversation_id)
            # print(response)
            # return json.dumps(response)
            response_return = utility.ConvertToChatGPT(message=response, model=open_ai_response_model)
            return json.dumps(response_return)
