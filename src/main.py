# Standard Library Imports
import argparse
import configparser
import json
import os
import uvicorn

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# Third-Party Imports
import asyncio

# Local Imports
import claude
from gemini_webapi import GeminiClient
import utility

# Constants
CONFIG_FILE_NAME = "Config.conf"
CONFIG_FOLDER = os.getcwd()
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, CONFIG_FILE_NAME)

# FastAPI application instance
app = FastAPI()

# Global variables
COOKIE_CLAUDE = None
COOKIE_GEMINI = None
GEMINI_CLIENT = None
CLAUDE_CLIENT = None

# Initialize AI models and cookies
async def initialize_ai_models(config_file_path: str):
    global COOKIE_CLAUDE, COOKIE_GEMINI, GEMINI_CLIENT, CLAUDE_CLIENT
    COOKIE_CLAUDE = utility.getCookie_Claude(configfilepath=config_file_path, configfilename=CONFIG_FILE_NAME)
    COOKIE_GEMINI = utility.getCookie_Gemini(configfilepath=config_file_path, configfilename=CONFIG_FILE_NAME)
    GEMINI_CLIENT = GeminiClient()
    CLAUDE_CLIENT = claude.Client(COOKIE_CLAUDE)
    await GEMINI_CLIENT.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True, verbose=False)

# Startup event handler
async def startup():
    await initialize_ai_models(CONFIG_FILE_PATH)

app.add_event_handler("startup", startup)

# Middleware for CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware for Web UI
@app.middleware("http")
async def web_ui_middleware(request: Request, call_next):
    response = await call_next(request)
    url = request.url.path.lower()
    if response.status_code == 404 and url == "/webai":
        index_html_path = os.path.join(os.path.dirname(__file__), "UI/build/index.html")
        return FileResponse(index_html_path)
    elif url == "/api/config":
        if os.path.exists(CONFIG_FILE_PATH):
            return JSONResponse(json.dumps(utility.ConfigINI_to_Dict(CONFIG_FILE_PATH)), status_code=200)
        else:
            return JSONResponse({"error": f"{CONFIG_FILE_PATH} Config file not found"})
    elif url == "/api/config/getclaudekey":
        if COOKIE_CLAUDE:
            return JSONResponse({"Claude": f"{COOKIE_CLAUDE}"}, status_code=200)
        return JSONResponse({"warning": "Failed to get Claude key"})
    elif url == "/api/config/getgeminikey":
        if COOKIE_GEMINI:
            return JSONResponse({"Gemini": f"{COOKIE_GEMINI}"}, status_code=200)
        return JSONResponse({"warning": "Failed to get Gemini key"})
    elif url == "/api/config/save":
        try:
            request_body = await request.json()
            model_name = request_body.get('Model')
            if not model_name:
                return JSONResponse({"error": "Model name not provided in request body"}, status_code=400)
            config = configparser.ConfigParser()
            config['Main'] = {}
            config['Main']['model'] = model_name
            with open(CONFIG_FILE_PATH, 'w') as configfile:
                config.write(configfile)
            return JSONResponse({"message": f"{model_name} saved successfully"}, status_code=200)
        except Exception as e:
            return JSONResponse({"error": f"Failed to save model: {str(e)}"}, status_code=500)
    return response

# API endpoints

@app.post("/gemini")
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
    
    if not GEMINI_CLIENT:
        return {"warning": "Looks like you're not logged in to Gemini. Please either set the Gemini cookie manually or log in to your gemini.google.com account through your web browser."}

    try:
        response = await GEMINI_CLIENT.generate_content(prompt=message.message)
        
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

@app.post("/claude")
async def ask_claude(request: Request, message: dict):
    """API endpoint to get Claude response.

    Args:
        request (Request): API request object.
        message (Message): Message request object.

    Returns:
        str: JSON string of Claude response.

    """

    if not COOKIE_CLAUDE:
        # cookie = os.environ.get("CLAUDE_COOKIE")
        return {"warning": "Looks like you're not logged in to Claude. Please either set the Claude cookie manually or log in to your Claude.ai account through your web browser."}
    
    # This IF statement may not be necessary.
    # It checks if the conversation_id is set to "string"
    # when default parameters are sent via Swagger UI (localhost:8000/docs).
    # If so, it sets the conversation_id to None.
    if message.conversation_id == "string":
        message.conversation_id = None
    
    conversation_id = message.conversation_id
    original_conversation_id = copy.deepcopy(conversation_id)

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
        # after the creation, you need to wait some time before to send
        await asyncio.sleep(2)

    if message.stream:
        res = CLAUDE_CLIENT.stream_message(message.message, conversation_id)
        # print(res)
        return StreamingResponse(
                res,
                media_type="text/event-stream",
            )
        await asyncio.sleep(0)
    else:
        res = CLAUDE_CLIENT.send_message(message.message, conversation_id)
        # print(res)
        return res

@app.post("/v1/chat/completions")
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
    
    OpenAIResponseModel = ResponseModel()
    
    conversation_id = None
    
    if OpenAIResponseModel == "Gemini":
        
        if not GEMINI_CLIENT:
            return {"warning": "Looks like you're not logged in to Gemini. Please either set the Gemini cookie manually or log in to your gemini.google.com account through your web browser."}
        
        try:
            response = await GEMINI_CLIENT.generate_content(prompt=message.message)
            return utility.ConvertToChatGPT(message=response, model=OpenAIResponseModel)
        
        except Exception as req_err:
            print(f"Error Occurred: {req_err}")
            return f"Error Occurred: {req_err}"
    
    else:
        
        if not CLAUDE_CLIENT:
            # cookie = os.environ.get("CLAUDE_COOKIE")
            return {"warning": "Looks like you're not logged in to Claude. Please either set the Claude cookie manually or log in to your Claude.ai account through your web browser."}
        
        conversation_id = None

        try:
            if not conversation_id:
                conversation = CLAUDE_CLIENT.create_new_chat()
                conversation_id = conversation["uuid"]
        except Exception as e:
            print("ERROR: ", conversation)
            return ("ERROR: ", conversation)

        if message.stream:
            
            async def combined_data_stream():
                async for item in CLAUDE_CLIENT.stream_message(message.message, conversation_id):
                    yield item

            async def combined_stream_with_json_format():
                async for item in combined_data_stream():
                    # Convert the data to ChatGPT JSON format
                    async for chunk in utility.claudeToChatGPTStream(item, OpenAIResponseModel):
                        yield chunk + "\n"
            
            return StreamingResponse(content=combined_stream_with_json_format(), media_type="text/plain")
            
            # response = CLAUDE_CLIENT.stream_message(message.message, conversation_id)
            # if type(response) != "string":
            #     return StreamingResponse(
            #                 'Error: Hourly limit may have been reached: ' + str(response),
            #                 media_type="text/event-stream",
            #             )
        else:
            # If streaming is not requested, return data as a normal response
            data = CLAUDE_CLIENT.send_message(message.message, conversation_id)
            # Convert the data to ChatGPT JSON format
            chatgpt_data = []
            async for chunk in utility.claudeToChatGPTStream(data, OpenAIResponseModel):
                chatgpt_data.append(chunk)
            return chatgpt_data[0]

# Serve UI files
app.mount('/', StaticFiles(directory="src/UI/build"), 'static')

# Run UVicorn server
def run_server(args):
    print("Welcome to WebAI to API:\n\nConfiguration      : http://localhost:8000/WebAI\nSwagger UI (Docs)  : http://localhost:8000/docs\n\n----------------------------------------------------------------\n\nAbout:\n    Learn more about the project: https://github.com/amm1rr/WebAI-to-API/\n")
    uvicorn.run("main:app", host=args.host, port=args.port, reload=args.reload)

# Main function
def main():
    parser = argparse.ArgumentParser(description="Run the UVicorn server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()
    run_server(args)

if __name__ == "__main__":
    main()
