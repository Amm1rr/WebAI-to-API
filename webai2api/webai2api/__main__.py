from .main import app, config_ui_path
from .routes.claude_routes import router as claude_router
from .routes.gemini_routes import router as gemini_router
from .routes.v1_routes import router as v1_router
from .routes.http_routes import web_ui_middleware
from .utils import utility

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import argparse
import uvicorn
import logging
import os

utility.configure_logging()
logging.info("main.py")

app = FastAPI()

COOKIE_GEMINI = utility.getCookie_Gemini()
COOKIE_CLAUDE = utility.getCookie_Claude(configfilepath=os.getcwd(), configfilename="Config.conf")

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
app.mount('/', StaticFiles(directory="webai2api/UI/build"), 'static')


# Middleware for Web UI
@app.middleware("http")
async def webmiddleware(request: Request, call_next):
    logging.info("main.py.web_middleware")
    response = await call_next(request)
    res = await web_ui_middleware(request=request, response=response, url=request.url.path.lower())
    return res


# Run uvicorn server
def run_server(args):
    logging.info("run.__main__.py")
    print(
        """
        
        Welcome to WebAI to API:

        Configuration      : http://localhost:8000/WebAI
        Swagger UI (Docs)  : http://localhost:8000/docs
        
        ----------------------------------------------------------------
        
        About:
            Learn more about the project: https://github.com/amm1rr/WebAI-to-API/
        
        """
    )
    # print("Welcome to WebAI to API:\n\nConfiguration      : http://localhost:8000/WebAI\nSwagger UI (Docs)  :
    # http://localhost:8000/docs\n\n----------------------------------------------------------------\n\nAbout:\n
    # Learn more about the project: https://github.com/amm1rr/WebAI-to-API/\n")
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)

if __name__ == "__main__":
    logging.info("__main__.py./__name__()")
    parser = argparse.ArgumentParser(description="Run the server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()
    run_server(args)
