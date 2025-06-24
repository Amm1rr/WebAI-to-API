# src/run.py
import argparse
import asyncio
import uvicorn
from fastapi.routing import APIRoute

# Import primary app and initialization function
from app.config import load_config
from app.main import app as webai_app
from app.services.gemini_client import init_gemini_client

# Conditionally import g4f runner function
try:
    from g4f.api import run_api as run_g4f_api
    G4F_AVAILABLE = True
except ImportError:
    G4F_AVAILABLE = False

def print_server_info(host: str, port: int, mode: str):
    """
    Displays formatted information about the server based on the running mode.
    """
    protocol = "http"
    base_url = f"{protocol}://{host}:{port}"
    
    print("\n" + "="*60)
    
    if mode == "webai":
        print("🚀 WebAI-to-API Server Initialized (Primary Mode) 🚀".center(60))
        print("="*60)
        print("\n✨ Available Services:")
        print(f"  - Docs (Swagger): {base_url}/docs")
        
        print("\n⚙️ Config.conf:")
        CONFIG = load_config()
        print(f"  - Browser: {CONFIG['Browser']['name']}")
        print(f"  - Model: {CONFIG['AI']['default_model_gemini']}")
        
        print("\n🔗 API Endpoints:")
        paths = sorted(list(set(route.path for route in webai_app.routes if isinstance(route, APIRoute))))
        for path in paths:
            if path.startswith("/") and path not in ["/docs", "/redoc", "/openapi.json"]:
                print(f"  - {base_url}{path}")
    
    elif mode == "g4f":
        print("🚀 G4F Fallback Server Initialized 🚀".center(60))
        print("="*60)
        g4f_base_url = f"{base_url}/v1"
        print("\n✨ G4F Service Info:")
        print(f"  - Base URL: {g4f_base_url}")
        print(f"  - Docs (Swagger): {base_url}/docs")

        print("\n🔍 API Discovery Endpoints:")
        print("  Use these endpoints to explore available models and providers:\n")
        print(f"  - Models   : {g4f_base_url}/models")
        print(f"  - Providers: {g4f_base_url}/providers")

        print("\n🔗 Main API Endpoints:")
        print(f"  - Chat Completions: {g4f_base_url}/chat/completions")
        print(f"  - Image Generation: {g4f_base_url}/images/generate")

    print("\n" + "="*60)
    print("Starting Server... (Press CTRL+C to quit)")
    print("="*60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=6969, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()

    print("INFO:     Attempting to initialize WebAI-to-API with Gemini cookies...")
    
    # Run the single async function to check for cookies and get the result
    initialization_successful = asyncio.run(init_gemini_client())

    # Now, from a standard synchronous context, run the appropriate server
    if initialization_successful:
        print("INFO:     ✅ Gemini client initialized successfully. Starting WebAI-to-API server.")
        print_server_info(args.host, args.port, mode="webai")
        uvicorn.run(webai_app, host=args.host, port=args.port, reload=args.reload)
    else:
        print("WARN:     ⚠️ Failed to initialize Gemini client. Falling back to g4f API server.")
        if not G4F_AVAILABLE:
            print("ERROR:    g4f library is not installed. Please run 'poetry add g4f' to use the fallback mode.")
        else:
            print_server_info(args.host, args.port, mode="g4f")
            # The 'reload' argument is not standard for g4f's run_api
            run_g4f_api(host=args.host, port=args.port)