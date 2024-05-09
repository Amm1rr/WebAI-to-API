from webai2api.__main__ import app
from webai2api.utils import utility
import logging

utility.configure_logging()
logging.info("run.py")


def run():
    import uvicorn
    logging.info("run.py.run()")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    logging.info("run.py.name()")
    run()
