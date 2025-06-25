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
        print("🚀 G4F Server Initialized 🚀".center(60))
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

def get_user_choice(webai_available: bool, g4f_available: bool) -> str | None:
    """
    Presents an interactive menu to the user and returns their choice.
    """
    print("\n" + "~"*60)
    print("Please select the server to run:".center(60))
    print("~"*60)

    options = {}
    if webai_available:
        print("  [1] WebAI-to-API (Requires valid Gemini cookies)")
        options['1'] = 'webai'
    if g4f_available:
        print("  [2] G4F Server (Fallback, no cookies required)")
        options['2'] = 'g4f'

    if not options:
        print("\nERROR: No server modes are available to run.")
        print("       - WebAI mode requires valid cookies.")
        print("       - G4F mode requires the 'g4f' library to be installed.")
        return None

    while True:
        try:
            choice = input(f"Enter your choice ({', '.join(options.keys())}) or 'q' to quit: ")
            if choice in options:
                return options[choice]
            elif choice.lower() == 'q':
                return None
            else:
                print(f"Invalid choice. Please enter one of the following: {', '.join(options.keys())}")
        except KeyboardInterrupt:
            print("\nExiting.")
            return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=6969, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()

    # --- Main Logic ---

    # Step 1: Determine which modes are available.
    print("INFO:     Checking availability of server modes...")
    webai_is_available = asyncio.run(init_gemini_client())
    
    if webai_is_available:
        print("INFO:     ✅ WebAI-to-API mode is available (Gemini cookies found).")
    else:
        print("WARN:     ⚠️ WebAI-to-API mode is not available (Could not initialize Gemini client).")
        
    if G4F_AVAILABLE:
        print("INFO:     ✅ G4F mode is available ('g4f' library is installed).")
    else:
        print("WARN:     ⚠️ G4F mode is not available ('g4f' library not found).")

    # Step 2: Get the user's choice from the interactive menu.
    chosen_mode = get_user_choice(webai_is_available, G4F_AVAILABLE)

    # Step 3: Run the selected server.
    if chosen_mode == "webai":
        print_server_info(args.host, args.port, mode="webai")
        uvicorn.run(webai_app, host=args.host, port=args.port, reload=args.reload)
        
    elif chosen_mode == "g4f":
        print_server_info(args.host, args.port, mode="g4f")
        run_g4f_api(host=args.host, port=args.port)

    else:
        print("\nNo server selected. Exiting program.")

