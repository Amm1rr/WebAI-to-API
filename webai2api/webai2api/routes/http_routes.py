import configparser
import json
import logging
import os

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from gemini_webapi import GeminiClient

from ..main import config_ui_path
from ..models.claude import Client
from ..utils import utility

utility.configure_logging()
logging.info("http_routes.py")

global COOKIE_CLAUDE
global COOKIE_GEMINI
global GEMINI_CLIENT
global CLAUDE_CLIENT


def initialize_cookies():
    logging.info("http_routes.py.initialize_cookies")
    global COOKIE_CLAUDE, COOKIE_GEMINI, GEMINI_CLIENT, CLAUDE_CLIENT
    COOKIE_CLAUDE = utility.getCookie_Claude(configfilepath=os.getcwd(), configfilename=utility.CONFIG_FILE_NAME)
    COOKIE_GEMINI = utility.getCookie_Gemini()
    CLAUDE_CLIENT = Client(COOKIE_CLAUDE)
    GEMINI_CLIENT = GeminiClient()


initialize_cookies()


async def web_ui_middleware(request: Request, response: utility.ResponseModel, url: str):
    logging.info("http_routes.py.web_ui_middleware")
    url = url.lower()
    if response.status_code == 404 and url == "/webai":
        index_html_path = config_ui_path()
        return FileResponse(index_html_path)
    elif url == "/api/config":
        if os.path.exists(utility.CONFIG_FILE_PATH):

            config_parse = utility.ConfigINI_to_Dict(utility.CONFIG_FILE_PATH)

            if '[Main]' not in config_parse:
                config_parse['Main'] = {}

            if 'model' not in config_parse['Main']:
                config_parse['Main']['model'] = utility.ResponseModel(utility.CONFIG_FILE_PATH)

            if '[Gemini]' not in config_parse:

                if COOKIE_GEMINI:
                    cookie_gemini_json = json.loads(COOKIE_GEMINI)

                    if 'Gemini' not in config_parse:
                        config_parse['Gemini'] = {}

                    # Check and assign values
                    if 'SESSION_ID' not in config_parse['Gemini']:
                        config_parse['Gemini']['SESSION_ID'] = cookie_gemini_json[0][1]

                    if 'SESSION_IDTS' not in config_parse['Gemini']:
                        config_parse['Gemini']['SESSION_IDTS'] = cookie_gemini_json[1][1]

                    if 'SESSION_IDCC' not in config_parse['Gemini']:
                        config_parse['Gemini']['SESSION_IDCC'] = cookie_gemini_json[2][1]

                    # return JSONResponse(config_parse, status_code=200)

                # return JSONResponse({"warning": "Failed to get Gemini key"})

            if '[Claude]' not in config_parse:
                if COOKIE_CLAUDE:

                    if 'Claude' not in config_parse:
                        config_parse['Claude'] = {}

                    # Check and assign values
                    if 'Cookie' not in config_parse['Claude']:
                        config_parse['Claude']['Cookie'] = COOKIE_CLAUDE

                    # return JSONResponse(config_parse, status_code=200)

            return JSONResponse(json.dumps(config_parse), status_code=200)
        else:
            return JSONResponse({"error": f"{utility.CONFIG_FILE_PATH} Config file not found"})
    elif url == "/api/config/getclaudekey":
        if COOKIE_CLAUDE:
            return JSONResponse({"Claude": f"{COOKIE_CLAUDE}"}, status_code=200)
        return JSONResponse({"warning": "Failed to get Claude key"})
    elif url == "/api/config/getgeminikey":
        if COOKIE_GEMINI:
            return JSONResponse({"Gemini": f"{COOKIE_GEMINI}"}, status_code=200)
        return JSONResponse({"warning": "Failed to get Gemini key"})
    elif url == "/api/config/save":
        # try:
        request_body = await request.json()
        model_name = request_body.get('Model')
        if not model_name:
            return JSONResponse({"error": "Model name not provided in request body"}, status_code=400)
        config = configparser.ConfigParser()
        config['Main'] = {}
        config['Main']['model'] = model_name
        with open(utility.CONFIG_FILE_PATH, 'w') as configfile:
            config.write(configfile)
        return JSONResponse({"message": f"{model_name} saved successfully"}, status_code=200)
        # except Exception as e:
        #     print(JSONResponse({"error": f"Failed to save model: {str(e)}"}, status_code=500))
        #     raise Exception
    return response
