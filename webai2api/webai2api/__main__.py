# from .main import app
from webai2api.main import app, Config_UI_Path
from .routes.claude_routes import router as claude_router
from .routes.gemini_routes import router as gemini_router
from .routes.v1_routes import router as v1_router
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
import uvicorn
import logging
import webai2api.utils.utility
from webai2api.utils.utility import ConfigINI_to_Dict, CONFIG_FILE_PATH, ResponseModel, getCookie_Claude, getCookie_Gemini
from fastapi.staticfiles import StaticFiles
import os, json, configparser

logging.basicConfig(level=logging.INFO)
logging.info("main.py")

app = FastAPI()

COOKIE_GEMINI = getCookie_Gemini(configfilepath=os.getcwd(), configfilename="Config.conf")
COOKIE_CLAUDE = getCookie_Claude(configfilepath=os.getcwd(), configfilename="Config.conf")

# Middleware for CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(claude_router)
app.include_router(gemini_router)
app.include_router(v1_router)

# Serve UI files
# app.mount('/', StaticFiles(directory=Config_UI_Path()), 'static')
app.mount('/', StaticFiles(directory="webai2api/UI/build"), 'static')

# Middleware for Web UI
@app.middleware("http")
async def web_ui_middleware(request: Request, call_next):
    logging.info("main.py./http")
    response = await call_next(request)
    url = request.url.path.lower()
    if response.status_code == 404 and url == "/webai":
        index_html_path = os.path.join(os.path.dirname(__file__), "UI/build/index.html")
        return FileResponse(index_html_path)
    elif url == "/api/config":
        if os.path.exists(CONFIG_FILE_PATH):
            
            config_parse = ConfigINI_to_Dict(CONFIG_FILE_PATH)
            
            if '[Main]' not in config_parse:
                config_parse['Main'] = {}
                
            if 'model' not in config_parse['Main']:
                config_parse['Main']['model'] = ResponseModel(CONFIG_FILE_PATH)
            
            if '[Gemini]' not in config_parse:
                if COOKIE_GEMINI:
                    COOKIE_GEMINI_json = json.loads(COOKIE_GEMINI)
                    
                    if 'Gemini' not in config_parse:
                        config_parse['Gemini'] = {}

                    # Check and assign values
                    if 'SESSION_ID' not in config_parse['Gemini']:
                        config_parse['Gemini']['SESSION_ID'] = COOKIE_GEMINI_json[0][1]

                    if 'SESSION_IDTS' not in config_parse['Gemini']:
                        config_parse['Gemini']['SESSION_IDTS'] = COOKIE_GEMINI_json[1][1]

                    if 'SESSION_IDCC' not in config_parse['Gemini']:
                        config_parse['Gemini']['SESSION_IDCC'] = COOKIE_GEMINI_json[2][1]
                    
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
        # try:
        request_body = await request.json()
        model_name = request_body.get('Model')
        if not model_name:
            return JSONResponse({"error": "Model name not provided in request body"}, status_code=400)
        config = configparser.ConfigParser()
        print("Config   : {}".format(model_name))
        config['Main'] = {}
        config['Main']['model'] = model_name
        with open(CONFIG_FILE_PATH, 'w') as configfile:
            config.write(configfile)
        return JSONResponse({"message": f"{model_name} saved successfully"}, status_code=200)
        # except Exception as e:
        #     print(JSONResponse({"error": f"Failed to save model: {str(e)}"}, status_code=500))
        #     raise Exception
    return response

def run():
    logging.info("run.__main__.py")
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    run()
    logging.info("__main__.py./__name__()")    
