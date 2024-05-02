from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from ..models.claude import Client
from ..utils.utility import getCookie_Claude, claudeToChatGPTStream, getCookie_Gemini, geminiToChatGPTStream, ResponseModel, ConvertToChatGPT, CONFIG_FILE_NAME, CONFIG_FILE_PATH
from gemini_webapi import GeminiClient
import logging
import copy
import asyncio
import os


logging.basicConfig(level=logging.INFO)
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
    global COOKIE_CLAUDE, COOKIE_GEMINI, GEMINI_CLIENT, CLAUDE_CLIENT
    COOKIE_CLAUDE = getCookie_Claude(configfilepath=config_file_path, configfilename=CONFIG_FILE_NAME)
    COOKIE_GEMINI = getCookie_Gemini(configfilepath=config_file_path, configfilename=CONFIG_FILE_NAME)
    CLAUDE_CLIENT = Client(COOKIE_CLAUDE)
    GEMINI_CLIENT = GeminiClient()
    try:
        await GEMINI_CLIENT.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True, verbose=False)
    except Exception as e:
        print(e)

# Startup event handler
async def startup():
    await initialize_ai_models(CONFIG_FILE_PATH)

router.add_event_handler("startup", startup)



@router.post("/chat")
async def ask_test(request: Request):
    logging.info("v1_routes.py.ask_test()")
    return {"Yes"}

@router.post("/v1/chat/completions")
async def ask_ai(request: Request, message: dict):
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
    
    try:
        logging.info("/v1/chat/completions")
    except:
        logging.info("/v1/chat/completions / EXCEPT")

    if 'messages' not in message:
        print("ERROR: 'messages' key is missing in the message dictionary")
        # yield "ERROR: 'messages' key is missing in the message dictionary"
        # raise ValueError("'messages' key is missing in the message dictionary")
        return("ERROR: 'messages' key is missing in the message dictionary")

    messages = message.get('messages', [])
    
    # Check if 'messages' is empty
    if not messages:
        print("'messages' key is empty")
        return("'messages' key is empty")
        # raise ValueError("'messages' key is empty")
    
    user_message_content = None
    conversation_id = None
    original_conversation_id = None
    stream = False
    model = None
    

    for msg in messages:
        if msg['role'] == "user":
            user_message_content = msg['content']

        # conversation_id = message.get('conversation_id')
        elif message['conversation_id']:
            message['conversation_id'] = None
            conversation_id = None
            
        elif conversation_id == "string":
            message['conversation_id'] = None
            conversation_id = None
            
        elif msg['stream']:
            stream = message.get('stream', False)

        elif msg['model']:
            model = message.get('model', "gpt-3.5-turbo")
        

    if user_message_content is None:
        user_message_content = message.get('message')

    if conversation_id is not None:
        original_conversation_id = copy.deepcopy(conversation_id)
    

    if not (user_message_content):
        # yield("Warning : Prompt is empty")
        print("Warning : Prompt is empty")
        return("Warning : Prompt is empty")
    
    
    OpenAIResponseModel = ResponseModel(CONFIG_FILE_PATH)
    
    print("Model:", model)
    print("Prompt:", user_message_content)
    print("Stream:", stream)
    print("Conversation ID:", conversation_id)
    print("OpenAIResponseModel: ", OpenAIResponseModel)
    
    prompt = user_message_content
    
    
    if OpenAIResponseModel == "Gemini":
        
        print("GEMINI")
        
        if not GEMINI_CLIENT:
            print("warning: Looks like you're not logged in to Gemini. Please either set the Gemini cookie manually or log in to your gemini.google.com account through your web browser.")
            return
        
        try:
            response = await GEMINI_CLIENT.generate_content(prompt=prompt)
            response_return = ConvertToChatGPT(message=response, model=OpenAIResponseModel)
            # print(response_return)
            # yield json.dumps(response_return)
            return response_return
        
        except Exception as req_err:
            print(f"Error Occurred: {req_err}")
            # raise
            return
    
    else:
        print("CLAUDE")
        
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
                            return ("error: ", e)
                        else:
                            print("Retrying in 1 second...")
                            await asyncio.sleep(1)
                else:
                    break
            except Exception as e:
                return ("error: ", e)

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
            return response
