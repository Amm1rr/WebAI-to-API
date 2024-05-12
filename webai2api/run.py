import uvicorn
from webai2api.__main__ import app
from webai2api.utils import utility
import logging

utility.configure_logging()
logging.info(__name__)


def run():
    logging.info(__name__ + ".run()")
    try:
        uvicorn.run(app, host="127.0.0.1", port=8000)
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")


if __name__ == "__main__":
    logging.info(__name__ + ".name()")
    run()
