# src/app/shutdown_transport.py
"""
Windows-only loopback graceful-shutdown transport.

A minimal control channel that lets a local process (the future Windows
updater) request graceful shutdown of the running server:

    client -> 127.0.0.1:<ephemeral port> -> ApplicationServer.request_shutdown("ipc")

Design contract (Phase 4):
- stdlib only, two verbs, one line per direction
- listener binds strictly to 127.0.0.1 with an OS-assigned ephemeral port
- random per-process token gates both commands
- `IDENTIFY <token>` returns the listener PID without side effects
- `SHUTDOWN <token>` invokes the application shutdown callback
- endpoint identity is published atomically to
  <RUNTIME_DIR>/shutdown-control.json as {"port": <int>, "token": "<hex>",
  "pid": <int>}
- cleanup removes the control file only if it still belongs to this
  listener (token match); stale files after force-kill are harmless

This module never touches BrowserEngine, sessions, or lifespan state.
"""

import json
import os
import secrets
import socket
import threading

CONTROL_HOST = "127.0.0.1"
MAX_REQUEST_BYTES = 256
MAX_RESPONSE_BYTES = 32
ACCEPT_TIMEOUT_SECONDS = 0.25
IO_TIMEOUT_SECONDS = 2.0
THREAD_JOIN_TIMEOUT_SECONDS = 5.0

_OK = b"OK\n"
_RETRY = b"RETRY\n"


class ShutdownTransportError(Exception):
    """Client-side transport failure (bad metadata, connect/IO failure)."""


class ShutdownListener:
    """Loopback TCP listener serving authenticated shutdown/identity commands.

    The callback receives the literal reason string "ipc" and must be safe
    to call from a foreign thread (ApplicationServer.request_shutdown is).
    """

    def __init__(self, callback, control_file):
        self._callback = callback
        self._control_file = control_file
        self._token = secrets.token_hex(16)
        self._sock = None
        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        """Bind, listen, publish control metadata atomically, run thread.

        Transactional: any failure closes the socket, removes owned/temp
        control metadata, resets internal state, and raises
        ShutdownTransportError (the original double-start RuntimeError
        excepted). No half-started listener is ever observable.
        """
        if self._thread is not None:
            raise RuntimeError("ShutdownListener already started")
        self._stop_event.clear()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        thread = None
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((CONTROL_HOST, 0))
            sock.listen(4)
            port = sock.getsockname()[1]
            self._publish_control_file(port)
            # Assign state before starting the thread: _serve dereferences
            # self._sock immediately.
            self._sock = sock
            thread = threading.Thread(
                target=self._serve,
                name="webai-shutdown-listener",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        except ShutdownTransportError:
            self._abort_start(sock, thread)
            raise
        except Exception as error:
            self._abort_start(sock, thread)
            raise ShutdownTransportError(
                f"Cannot start shutdown listener: {error!r}"
            ) from error


    def _abort_start(self, sock, thread):
        """Best-effort rollback of a partially started listener."""
        self._stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(THREAD_JOIN_TIMEOUT_SECONDS)
        try:
            sock.close()
        except OSError:
            pass
        self._unlink_temp()
        self._remove_owned_control_file()
        self._sock = None
        self._thread = None

    def _publish_control_file(self, port):
        parent = os.path.dirname(self._control_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = json.dumps(
            {"port": port, "token": self._token, "pid": os.getpid()}
        ).encode("utf-8")
        temp_file = self._control_file + ".tmp"
        with open(temp_file, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Same-directory rename: atomic on both POSIX and Windows.
        os.replace(temp_file, self._control_file)

    def _unlink_temp(self):
        try:
            os.unlink(self._control_file + ".tmp")
        except OSError:
            pass

    def stop(self):
        """Idempotent teardown: close socket, join thread, remove owned file."""
        self._stop_event.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(THREAD_JOIN_TIMEOUT_SECONDS)
        self._thread = None
        self._sock = None
        self._remove_owned_control_file()

    def _remove_owned_control_file(self):
        try:
            with open(self._control_file, "rb") as handle:
                data = json.loads(handle.read())
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("token") != self._token:
            return  # missing/malformed/replaced by a newer listener
        try:
            os.unlink(self._control_file)
        except OSError:
            pass

    # --- listener thread -------------------------------------------------

    def _serve(self):
        while not self._stop_event.is_set():
            try:
                self._sock.settimeout(ACCEPT_TIMEOUT_SECONDS)
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # listening socket closed by stop()
            try:
                self._handle_connection(conn)
            except OSError:
                pass  # connection-level failure must not kill the listener
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle_connection(self, conn):
        conn.settimeout(IO_TIMEOUT_SECONDS)
        chunks = []
        received = 0
        while received <= MAX_REQUEST_BYTES:
            chunk = conn.recv(MAX_REQUEST_BYTES + 1 - received)
            if not chunk:
                return  # peer closed before completing a request
            chunks.append(chunk)
            received += len(chunk)
            if b"\n" in chunk:
                break
        else:
            return  # oversized / unterminated request: reject silently
        data = b"".join(chunks)
        newline_index = data.find(b"\n")
        if newline_index == -1:
            return
        line = data[:newline_index].decode("utf-8", errors="replace").strip()
        parts = line.split(" ")
        if len(parts) != 2 or parts[1] != self._token:
            return  # wrong token: no callback, no response
        if parts[0] == "IDENTIFY":
            conn.sendall(f"PID {os.getpid()}\n".encode("ascii"))
            return
        if parts[0] != "SHUTDOWN":
            return  # malformed command: no callback, no response
        response = _OK if self._callback("ipc") else _RETRY
        conn.sendall(response)


def _read_control_metadata(control_file, validate_pid=False):
    try:
        with open(control_file, "rb") as handle:
            raw = handle.read()
        metadata = json.loads(raw)
        port = metadata["port"]
        token = metadata["token"]
    except FileNotFoundError as error:
        raise ShutdownTransportError(
            f"Shutdown control file not found: {control_file}"
        ) from error
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ShutdownTransportError(
            f"Invalid shutdown control file {control_file}: {error!r}"
        ) from error
    if isinstance(port, bool) or not isinstance(port, int) or port <= 0:
        raise ShutdownTransportError(
            f"Invalid shutdown control file {control_file}: bad port"
        )
    if not isinstance(token, str) or not token:
        raise ShutdownTransportError(
            f"Invalid shutdown control file {control_file}: bad token"
        )
    if validate_pid and isinstance(metadata, dict) and "pid" in metadata:
        pid = metadata["pid"]
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ShutdownTransportError(
                f"Invalid shutdown control file {control_file}: bad pid"
            )
    return port, token


def send_shutdown(control_file, timeout=3.0):
    """Send the shutdown command; returns "ok" or "retry".

    Raises ShutdownTransportError for missing/invalid metadata, connection
    failures, timeouts, or unexpected responses. Legacy metadata without a
    PID remains valid; a PID is validated when present.
    """
    port, token = _read_control_metadata(control_file, validate_pid=True)

    try:
        with socket.create_connection((CONTROL_HOST, port), timeout=timeout) as conn:
            conn.settimeout(timeout)
            conn.sendall(f"SHUTDOWN {token}\n".encode("utf-8"))
            response = _recv_line(conn)
    except (OSError, TimeoutError) as error:
        raise ShutdownTransportError(
            f"Cannot reach shutdown endpoint on {CONTROL_HOST}:{port}: "
            f"{error!r}"
        ) from error

    text = response.decode("utf-8", errors="replace").strip().upper()
    if text == "OK":
        return "ok"
    if text == "RETRY":
        return "retry"
    raise ShutdownTransportError(
        f"Unexpected shutdown response: {response!r}"
    )


def identify_server(control_file, timeout=3.0):
    """Return listener-owned PID from an authenticated identity response."""
    port, token = _read_control_metadata(control_file)
    try:
        with socket.create_connection((CONTROL_HOST, port), timeout=timeout) as conn:
            conn.settimeout(timeout)
            conn.sendall(f"IDENTIFY {token}\n".encode("ascii"))
            response = _recv_line(conn)
    except (OSError, TimeoutError) as error:
        raise ShutdownTransportError(
            f"Cannot reach identity endpoint on {CONTROL_HOST}:{port}: "
            f"{error!r}"
        ) from error

    text = response.decode("ascii", errors="replace").strip()
    parts = text.split(" ")
    if len(parts) != 2 or parts[0] != "PID":
        raise ShutdownTransportError(
            f"Unexpected identity response: {response!r}"
        )
    pid_text = parts[1]
    if not pid_text.isascii() or not pid_text.isdigit():
        raise ShutdownTransportError(
            f"Invalid identity response: {response!r}"
        )
    pid = int(pid_text)
    if pid <= 0:
        raise ShutdownTransportError(
            f"Invalid identity response: {response!r}"
        )
    return pid


def _recv_line(conn):
    chunks = []
    received = 0
    while received < MAX_RESPONSE_BYTES:
        chunk = conn.recv(MAX_RESPONSE_BYTES - received)
        if not chunk:
            raise ShutdownTransportError(
                "Shutdown endpoint closed without a response"
            )
        chunks.append(chunk)
        received += len(chunk)
        if b"\n" in chunk:
            return b"".join(chunks)
    raise ShutdownTransportError(
        f"Shutdown response exceeded {MAX_RESPONSE_BYTES} bytes without a "
        "newline"
    )
