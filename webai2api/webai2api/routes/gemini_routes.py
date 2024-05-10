from fastapi import APIRouter, Request
from gemini_webapi import GeminiClient
from ..utils import utility
import logging
import copy
import json
from fastapi.responses import StreamingResponse

utility.configure_logging()
logging.info("gemini_routes.py")

router = APIRouter()

COOKIE_GEMINI = None
GEMINI_CLIENT = None

async def initialize_gemini():
    logging.info("gemini_routes.py./startup_event")
    import os
    global COOKIE_GEMINI, GEMINI_CLIENT
    COOKIE_GEMINI = utility.getCookie_Gemini()
    GEMINI_CLIENT = GeminiClient()
    try:
        await GEMINI_CLIENT.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True, verbose=False)
    except Exception as e:
        print(e)

@router.on_event("startup")
async def startup_event():
    await initialize_gemini()

@router.post("/gemini")
async def ask_gemini(request: Request, message: dict):
    """API endpoint to get response from Google Gemini.

    Args:
        request (Request): API request object
        message (Message): Message request object

    Returns:
        str: Gemini response

    Raises:
        ConnectionError: If internet connection or API server is unavailable
        HTTPError: If HTTP error response received from API
        RequestException: If other request error occurs
        Exception: For any other errors

    """
    logging.info("gemini_routes.py./gemini")
    
    conversation_id = message.get('conversation_id')
    if conversation_id == "string":
        message['conversation_id'] = None
        conversation_id = None
    
    original_conversation_id = copy.deepcopy(conversation_id)
    
    stream = message.get('stream', False)

    prompt = message.get('message', "What is your name?")
    
    if not GEMINI_CLIENT:
        return {"warning": "Looks like you're not logged in to Gemini. Please either set the Gemini cookie manually or log in to your gemini.google.com account through your web browser."}

    try:
        response = await GEMINI_CLIENT.generate_content(prompt=prompt)
        
        json_data = response.json()

        # Load the JSON data
        data = json.loads(json_data)

        ## Accessing elements:
        # metadata = data["metadata"] 
        candidates = data["candidates"]
        # chosen_index = data["chosen"]

        # Extract specific information from the first candidate
        # first_candidate_rcid = candidates[0]["rcid"]
        first_candidate_text = candidates[0]["text"]
        
        # print(first_candidate_text)
        # return first_candidate_text
        return StreamingResponse(
                    first_candidate_text,
                    media_type="text/event-stream",
                )
    
    except Exception as req_err:
        print(f"Error Occurred: {req_err}")
        return f"Error Occurred: {req_err}"

# @router.post("/gemini")
# async def ask_gemini(request: Request, message: dict):
#     logging.info("gemini_routes.py./gemini")
#     if not GEMINI_CLIENT:
#         yield {"warning": "Looks like you're not logged in to Gemini. Please either set the Gemini cookie manually or log in to your gemini.google.com account through your web browser."}

#     conversation_id = message.get('conversation_id')
#     if conversation_id == "string":
#         message['conversation_id'] = None
#         conversation_id = None

#     prompt = message.get('message', "What is your name?")

#     try:
#         response = await GEMINI_CLIENT.generate_content(prompt=prompt)
#         async for chunk in geminiToChatGPTStream(response, "gemini"):
#             yield chunk
#     except Exception as req_err:
#         print(f"Error Occurred: {req_err}")
#         yield f"Error Occurred: {req_err}"
    
#     return