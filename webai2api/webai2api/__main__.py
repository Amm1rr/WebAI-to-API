from webai2api.main import app, Config_UI_Path
from .routes.claude_routes import router as claude_router
from .routes.gemini_routes import router as gemini_router
from .routes.v1_routes import router as v1_router
from .routes.http_routes import web_ui_middleware
from webai2api.utils.utility import ConfigINI_to_Dict, CONFIG_FILE_PATH, ResponseModel, getCookie_Claude, getCookie_Gemini

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import logging
import os, json, configparser
import asyncio

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
async def webmiddleware(request: Request, call_next):
    logging.info("main.py.webmiddleware")
    response = await call_next(request)
    res = await web_ui_middleware(request=request, response=response, url=request.url.path.lower())
    return res

def run():
    logging.info("run.__main__.py")
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    run()
    logging.info("__main__.py./__name__()")    
