import argparse
import uvicorn
from webai2api.__main__ import app
from webai2api.utils import utility
import logging

utility.configure_logging()
logging.info(__name__)


# Run uvicorn server
def run_server(args):
    logging.debug("main.py./run_server()")
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
    try:
        uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
        logging.info(__name__ + ".run()")
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")


if __name__ == "__main__":
    logging.info(__name__ + ".name()")
    parser = argparse.ArgumentParser(description="Run the server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()
    run_server(args)
