# src/run.py
import argparse
import uvicorn
from app.config import load_config
from app.main import app
from fastapi.routing import APIRoute

def print_server_info(host: str, port: int):
    """
    Displays formatted information about the running server and its available endpoints.
    """
    protocol = "http"
    base_url = f"{protocol}://{host}:{port}"
    
    # Header
    print("\n" + "="*60)
    print("🚀 WebAI-to-API Server Initialized 🚀".center(60))
    print("="*60)
    
    # General Info and Documentation
    print("\n✨ Available Services:")
    print(f"  - Docs (Swagger): {base_url}/docs")
    
    print("\n⚙️ Config.conf:")
    CONFIG = load_config()
    print(f"  - Browser: {CONFIG['Browser']['name']}")
    print(f"  - Model: {CONFIG['AI']['default_model_gemini']}")
    
    # API Endpoints
    print("\n🔗 API Endpoints:")
    
    
    # Collect and sort unique paths from the app's routes
    # This ensures we don't list the same path multiple times
    paths = sorted(list(set(route.path for route in app.routes if isinstance(route, APIRoute))))
    # paths = sorted(list(set(route.path for route in app.routes if hasattr(route, "path"))))
    
    # Filter and print only the relevant API routes
    for path in paths:
        if path.startswith("/") and path not in ["/docs", "/redoc", "/openapi.json"]:
            print(f"  - {base_url}{path}")

    print("\n" + "="*60)
    print("Starting Uvicorn server... (Press CTRL+C to quit)")
    print("="*60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=6969, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()

    # Display the server information before Uvicorn starts its own logging
    print_server_info(args.host, args.port)
    
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)