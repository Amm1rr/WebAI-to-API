"""Minimal stdlib HTTP service used as the updater-managed service.

Serves /health 200 (or a forced failure status when the health-failure
marker file contains an integer status). Exits cleanly on SIGTERM/SIGINT so
POSIX graceful-stop flows observe a real drain. Writes a ready-file after
bind when READY_FILE is provided (startup-race tests).

Environment:
    HEALTH_FAIL_FILE   optional path; integer contents override /health status
    READY_FILE         optional path; touched once the socket is bound
"""

import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
FAIL_FILE = os.environ.get("HEALTH_FAIL_FILE", ".fail-health")
READY_FILE = os.environ.get("READY_FILE", "")


def _fail_status():
    try:
        with open(FAIL_FILE) as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return 0


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = 200
        if self.path != "/health":
            status = 404
        elif FAIL_FILE:
            forced = _fail_status()
            if forced:
                status = forced
        self.send_response(status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def _shutdown(signum, frame):
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

server = HTTPServer(("127.0.0.1", PORT), Handler)
if READY_FILE:
    with open(READY_FILE, "w") as handle:
        handle.write("ready")
server.serve_forever()
