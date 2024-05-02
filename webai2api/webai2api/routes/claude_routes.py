from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from ..models.claude import Client
from ..utils.utility import getCookie_Claude, claudeToChatGPTStream
import logging
import copy
import asyncio

logging.basicConfig(level=logging.INFO)

router = APIRouter()

COOKIE_CLAUDE = None
CLAUDE_CLIENT = None

async def initialize_claude():
    import os
    print(os.getcwd() + "/webai2api/utils")
    global COOKIE_CLAUDE, CLAUDE_CLIENT
    COOKIE_CLAUDE = getCookie_Claude(configfilepath=os.getcwd(), configfilename="Config.conf")
    CLAUDE_CLIENT = Client(COOKIE_CLAUDE)

@router.on_event("startup")
async def startup_event():
    logging.info("claude_routes.py./startup_event")
    await initialize_claude()

@router.post("/claude")
async def ask_claude(request: Request, message: dict):
    """API endpoint to get Claude response.

    Args:
        request (Request): API request object.
        message (Message): Message request object.

    Returns:
        str: JSON string of Claude response.

    """
    logging.info("main.py./ask_claude")

    if not COOKIE_CLAUDE:
        # cookie = os.environ.get("CLAUDE_COOKIE")
        return {"warning": "Looks like you're not logged in to Claude. Please either set the Claude cookie manually or log in to your Claude.ai account through your web browser."}
    
    # This IF statement may not be necessary.
    # It checks if the conversation_id is set to "string"
    # when default parameters are sent via Swagger UI (localhost:8000/docs).
    # If so, it sets the conversation_id to None.
    conversation_id = message.get('conversation_id')
    if conversation_id == "string":
        message['conversation_id'] = None
        conversation_id = None
    
    # conversation_id = message.conversation_id
    original_conversation_id = copy.deepcopy(conversation_id)
    
    stream = message.get('stream', False)

    prompt = message.get('message', "What is your name?")


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
        res = CLAUDE_CLIENT.stream_message(prompt, conversation_id)
        # print(res)
        return StreamingResponse(
                res,
                media_type="text/event-stream",
            )
        await asyncio.sleep(0)
    else:
        res = CLAUDE_CLIENT.send_message(prompt, conversation_id)
        # print(res)
        return res


# @router.post("/claude")
# async def ask_claude(request: Request, message: dict):
#     logging.info("claude_routes.py./claude")
#     if not COOKIE_CLAUDE:
#         yield {"warning": "Looks like you're not logged in to Claude. Please either set the Claude cookie manually or log in to your Claude.ai account through your web browser."}
#         return

#     conversation_id = message.get('conversation_id')
#     if conversation_id == "string":
#         message['conversation_id'] = None
#         conversation_id = None

#     prompt = message.get('message', "What is your name?")

#     if message.get('stream', False):
#         res = CLAUDE_CLIENT.stream_message(prompt, conversation_id)
#         yield StreamingResponse(res, media_type="text/event-stream")
#     else:
#         res = CLAUDE_CLIENT.send_message(prompt, conversation_id)
#         async for chunk in claudeToChatGPTStream(res, "claude"):
#             yield chunk
    
#     return
