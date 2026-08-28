import json
import os
import socket
import stat
import threading
from pathlib import Path

import pytest

from app.shutdown_transport import (
    ShutdownListener,
    ShutdownTransportError,
    identify_server,
    send_shutdown,
)


def make_listener(tmp_path, results=(True,), control_name="shutdown-control.json"):
    """Listener whose callback pops canned request_shutdown() results."""
    calls = []
    lock = threading.Lock()

    def callback(reason):
        with lock:
            calls.append(reason)
        return results[len(calls) - 1] if len(calls) <= len(results) else False

    control = str(tmp_path / control_name)
    listener = ShutdownListener(callback=callback, control_file=control)
    listener.start()
    return listener, calls


def raw_request(port, payload, read=True):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
        conn.settimeout(3)
        conn.sendall(payload)
        if not read:
            return None
        try:
            return conn.recv(16)
        except (ConnectionResetError, OSError):
            return b""


def control_port(control_file):
    with open(control_file, encoding="utf-8") as handle:
        return json.load(handle)["port"]


# --- Protocol ---------------------------------------------------------------


def test_valid_token_and_accepted_callback_responds_ok(tmp_path):
    listener, calls = make_listener(tmp_path, results=(True,))
    try:
        assert raw_request(control_port(listener._control_file),
                           f"SHUTDOWN {listener._token}\n".encode()) == b"OK\n"
        assert calls == ["ipc"]
    finally:
        listener.stop()


def test_callback_false_responds_retry(tmp_path):
    listener, calls = make_listener(tmp_path, results=(False,))
    try:
        assert raw_request(control_port(listener._control_file),
                           f"SHUTDOWN {listener._token}\n".encode()) == b"RETRY\n"
        assert calls == ["ipc"]
    finally:
        listener.stop()


def test_wrong_token_never_calls_callback(tmp_path):
    listener, calls = make_listener(tmp_path)
    try:
        assert raw_request(control_port(listener._control_file),
                           b"SHUTDOWN deadbeef\n") in (b"", None)
        assert calls == []
    finally:
        listener.stop()


@pytest.mark.parametrize("payload", [
    b"GARBAGE\n",
    b"SHUTDOWN\n",
    b"SHUTDOWN extra token parts\n",
    b"IDENTIFY\n",
    b"IDENTIFY extra token parts\n",
    b"shutdown wrong-case token\n",
])
def test_malformed_requests_rejected_silently(tmp_path, payload):
    listener, calls = make_listener(tmp_path)
    try:
        response = raw_request(control_port(listener._control_file), payload)
        assert response in (b"", None)
        assert calls == []
    finally:
        listener.stop()


def test_oversized_unterminated_request_rejected(tmp_path):
    listener, calls = make_listener(tmp_path)
    try:
        raw_request(control_port(listener._control_file), b"A" * 512)
        assert calls == []
    finally:
        listener.stop()


def test_multiple_sequential_requests_follow_callback_results(tmp_path):
    listener, calls = make_listener(tmp_path, results=(True, False))
    port = None
    try:
        port = control_port(listener._control_file)
        command = f"SHUTDOWN {listener._token}\n".encode()
        assert raw_request(port, command) == b"OK\n"
        assert raw_request(port, command) == b"RETRY\n"
        assert calls == ["ipc", "ipc"]
    finally:
        listener.stop()


def test_listener_survives_connection_level_failures(tmp_path):
    listener, calls = make_listener(tmp_path)
    try:
        port = control_port(listener._control_file)
        # Abrupt reset must not terminate the accept loop.
        with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                            __import__("struct").pack("ii", 1, 0))
            conn.sendall(b"SHUTDOWN x\n")
            conn.close()
        assert raw_request(port, f"SHUTDOWN {listener._token}\n".encode()) \
            is not None
    finally:
        listener.stop()


# --- Lifecycle ----------------------------------------------------------------


def test_control_file_published_after_listen_with_real_port_and_token(tmp_path):
    control = str(tmp_path / "pub.json")
    listener = ShutdownListener(lambda reason: True, control_file=control)
    listener.start()
    try:
        with open(control, encoding="utf-8") as handle:
            data = json.load(handle)  # parseable
        assert isinstance(data["port"], int) and data["port"] > 0
        assert isinstance(data["token"], str) and len(data["token"]) == 32
        assert data["token"] == listener._token
        assert data["pid"] == os.getpid()
    finally:
        listener.stop()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
def test_control_file_and_replacement_are_private(tmp_path, monkeypatch):
    parent = tmp_path / "runtime"
    parent.mkdir()
    os.chmod(parent, 0o755)
    control = parent / "shutdown-control.json"
    observed_temp_modes = []
    real_replace = os.replace

    def inspect_replace(source, destination):
        observed_temp_modes.append(stat.S_IMODE(os.stat(source).st_mode))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", inspect_replace)
    listener = ShutdownListener(lambda reason: True, control_file=str(control))
    listener.start()
    try:
        assert stat.S_IMODE(os.stat(parent).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(control).st_mode) == 0o600
        assert observed_temp_modes == [0o600]

        os.chmod(control, 0o644)
        listener._publish_control_file(12345)
        assert stat.S_IMODE(os.stat(control).st_mode) == 0o600
        assert observed_temp_modes == [0o600, 0o600]
    finally:
        listener.stop()


def test_stop_removes_owned_control_file(tmp_path):
    listener, _ = make_listener(tmp_path)
    control = listener._control_file
    assert os.path.exists(control)
    listener.stop()
    assert not os.path.exists(control)


def test_stop_is_idempotent(tmp_path):
    listener, _ = make_listener(tmp_path)
    listener.stop()
    listener.stop()  # must not raise


def test_stop_does_not_delete_newer_foreign_control_file(tmp_path):
    listener, _ = make_listener(tmp_path)
    control = listener._control_file
    foreign = {"port": 1, "token": "newer-process-token"}
    with open(control, "w", encoding="utf-8") as handle:
        json.dump(foreign, handle)
    listener.stop()
    with open(control, encoding="utf-8") as handle:
        assert json.load(handle) == foreign  # untouched
    os.unlink(control)


def test_start_publish_failure_leaves_no_final_metadata(tmp_path, monkeypatch):
    control = str(tmp_path / "fail.json")

    def broken_replace(src, dst):
        raise OSError("publish failed")

    monkeypatch.setattr(os, "replace", broken_replace)
    listener = ShutdownListener(lambda reason: True, control_file=control)
    with pytest.raises(ShutdownTransportError):
        listener.start()
    assert not os.path.exists(control)
    assert not os.path.exists(control + ".tmp")


def test_double_start_rejected(tmp_path):
    listener, _ = make_listener(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            listener.start()
    finally:
        listener.stop()


def test_listener_thread_exits_after_stop(tmp_path):
    listener, _ = make_listener(tmp_path)
    thread = listener._thread
    assert thread.is_alive()
    listener.stop()
    assert not thread.is_alive()


# --- Client -------------------------------------------------------------------


def test_send_shutdown_returns_ok(tmp_path):
    listener, _ = make_listener(tmp_path, results=(True,))
    try:
        assert send_shutdown(listener._control_file) == "ok"
    finally:
        listener.stop()


def test_identify_returns_listener_pid_without_shutdown_side_effect(tmp_path):
    listener, calls = make_listener(tmp_path)
    try:
        assert identify_server(listener._control_file) == os.getpid()
        assert calls == []
    finally:
        listener.stop()


def test_identify_accepts_legacy_metadata_without_pid(tmp_path):
    listener, calls = make_listener(tmp_path)
    try:
        with open(listener._control_file, encoding="utf-8") as handle:
            metadata = json.load(handle)
        metadata.pop("pid")
        with open(listener._control_file, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle)

        assert identify_server(listener._control_file) == os.getpid()
        assert calls == []
    finally:
        listener.stop()


def test_identify_wrong_token_does_not_identify(tmp_path):
    listener, calls = make_listener(tmp_path)
    try:
        assert raw_request(
            control_port(listener._control_file),
            b"IDENTIFY wrong-token\n",
        ) in (b"", None)
        assert calls == []
    finally:
        listener.stop()


def test_send_shutdown_accepts_legacy_metadata_without_pid(tmp_path):
    listener, calls = make_listener(tmp_path, results=(True,))
    try:
        with open(listener._control_file, encoding="utf-8") as handle:
            metadata = json.load(handle)
        metadata.pop("pid")
        with open(listener._control_file, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle)

        assert send_shutdown(listener._control_file) == "ok"
        assert calls == ["ipc"]
    finally:
        listener.stop()


def test_send_shutdown_normalizes_retry(tmp_path):
    listener, _ = make_listener(tmp_path, results=(False,))
    try:
        assert send_shutdown(listener._control_file) == "retry"
    finally:
        listener.stop()


def test_missing_control_file_raises_clean_error(tmp_path):
    with pytest.raises(ShutdownTransportError, match="not found"):
        send_shutdown(str(tmp_path / "absent.json"))


@pytest.mark.parametrize("content", [
    b"not json",
    b'{"token": "abc"}',          # missing port
    b'{"port": "x", "token": "t"}',
    b'{"port": -5, "token": "t"}',
    b'{"port": true, "token": "t"}',
    b'{"port": 12345}',
    b'{"port": 12345, "token": "t", "pid": true}',
    b'{"port": 12345, "token": "t", "pid": 0}',
    b'{"port": 12345, "token": "t", "pid": "42"}',
])
def test_malformed_control_files_raise_clean_error(tmp_path, content):
    control = tmp_path / "bad.json"
    control.write_bytes(content)
    with pytest.raises(ShutdownTransportError):
        send_shutdown(str(control))


def test_unavailable_endpoint_raises_clean_error(tmp_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()  # port now (almost certainly) unroutable
    control = tmp_path / "dead.json"
    control.write_text(
        json.dumps({"port": dead_port, "token": "t", "pid": os.getpid()})
    )
    with pytest.raises(ShutdownTransportError, match="reach"):
        send_shutdown(str(control))


def test_unexpected_response_raises_clean_error(tmp_path):
    """Fake server answers garbage; client normalizes to transport error."""
    fake = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    fake.bind(("127.0.0.1", 0))
    fake.listen(1)
    port = fake.getsockname()[1]
    control = tmp_path / "weird.json"
    control.write_text(
        json.dumps({"port": port, "token": "t", "pid": os.getpid()})
    )

    def garbage_server():
        conn, _ = fake.accept()
        conn.recv(64)
        conn.sendall(b"NONSENSE\n")
        conn.close()

    server_thread = threading.Thread(target=garbage_server, daemon=True)
    server_thread.start()
    try:
        with pytest.raises(ShutdownTransportError, match="Unexpected"):
            send_shutdown(str(control))
        server_thread.join(3)
    finally:
        fake.close()


@pytest.mark.parametrize(
    "payload",
    [b"PID nope\n", b"PID 0\n", b"PID 1 extra\n", b"NOPE\n"],
)
def test_identify_rejects_malformed_response(tmp_path, payload):
    control, fake, thread = _fake_responder(tmp_path, payload)
    try:
        with pytest.raises(ShutdownTransportError):
            identify_server(control)
        thread.join(3)
    finally:
        fake.close()


def test_identify_unavailable_endpoint_raises_clean_error(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()
    control = tmp_path / "dead-identity.json"
    control.write_text(json.dumps({"port": dead_port, "token": "t"}))

    with pytest.raises(ShutdownTransportError, match="identity endpoint"):
        identify_server(str(control))


# --- Fix 1: bounded client response reads ------------------------------------


def _fake_responder(tmp_path, payload, delay=0.0):
    """One-shot fake endpoint that sends an arbitrary raw response."""
    fake = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    fake.bind(("127.0.0.1", 0))
    fake.listen(1)
    port = fake.getsockname()[1]
    control = tmp_path / "raw.json"
    control.write_text(
        json.dumps({"port": port, "token": "t", "pid": os.getpid()})
    )

    def serve():
        conn, _ = fake.accept()
        conn.recv(64)
        if delay:
            import time

            time.sleep(delay)
        conn.sendall(payload)
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return str(control), fake, thread


def test_client_accepts_normal_ok_response(tmp_path):
    control, fake, thread = _fake_responder(tmp_path, b"OK\n")
    try:
        assert send_shutdown(control) == "ok"
        thread.join(3)
    finally:
        fake.close()


def test_client_accepts_normal_retry_response(tmp_path):
    control, fake, thread = _fake_responder(tmp_path, b"RETRY\n")
    try:
        assert send_shutdown(control) == "retry"
        thread.join(3)
    finally:
        fake.close()


def test_oversized_response_without_newline_rejected(tmp_path):
    control, fake, thread = _fake_responder(tmp_path, b"A" * 64)
    try:
        with pytest.raises(ShutdownTransportError, match="exceeded"):
            send_shutdown(control)
        thread.join(3)
    finally:
        fake.close()


def test_newline_after_limit_rejected(tmp_path):
    # Exactly at the limit: 32 bytes received, no newline yet -> reject
    # without reading further.
    control, fake, thread = _fake_responder(tmp_path, b"A" * 32 + b"\n")
    try:
        with pytest.raises(ShutdownTransportError, match="exceeded"):
            send_shutdown(control)
        thread.join(3)
    finally:
        fake.close()


def test_response_buffer_never_exceeds_limit(tmp_path):
    """recv window is capped: peer cannot make the client buffer more."""
    control, fake, thread = _fake_responder(tmp_path, b"B" * 4096)
    try:
        with pytest.raises(ShutdownTransportError):
            send_shutdown(control)
        thread.join(3)
    finally:
        fake.close()


# --- Fix 2: transactional start ----------------------------------------------


def test_thread_start_failure_is_transactional(tmp_path, monkeypatch):
    created = []
    real_socket = socket.socket

    def recording_socket(*args, **kwargs):
        sock = real_socket(*args, **kwargs)
        created.append(sock)
        return sock

    monkeypatch.setattr(
        "app.shutdown_transport.socket.socket", recording_socket
    )

    def broken_start(_self):
        raise RuntimeError("thread machinery unavailable")

    monkeypatch.setattr(threading.Thread, "start", broken_start)

    control = tmp_path / "transactional.json"
    listener = ShutdownListener(lambda reason: True, control_file=str(control))

    with pytest.raises(ShutdownTransportError, match="Cannot start"):
        listener.start()

    assert listener._sock is None
    assert listener._thread is None
    assert all(sock.fileno() == -1 for sock in created)  # sockets closed
    assert not control.exists()      # owned metadata rolled back
    assert not Path(str(control) + ".tmp").exists()
    listener.stop()                  # remains safe/idempotent
    listener.stop()


# --- Fix 3: parent-directory creation ----------------------------------------


def test_missing_nested_parent_directory_is_created(tmp_path):
    control = tmp_path / "deeply" / "nested" / "runtime" / "shutdown-control.json"
    listener = ShutdownListener(lambda reason: True, control_file=str(control))
    listener.start()
    try:
        assert control.exists()
        data = json.loads(control.read_text(encoding="utf-8"))
        assert isinstance(data["port"], int) and data["port"] > 0
        send_result = send_shutdown(str(control))
        assert send_result == "ok"
    finally:
        listener.stop()


def test_makedirs_failure_is_clean_transport_error(tmp_path, monkeypatch):
    control = tmp_path / "nope" / "shutdown-control.json"

    def broken_makedirs(path, exist_ok=False):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(os, "makedirs", broken_makedirs)
    listener = ShutdownListener(lambda reason: True, control_file=str(control))
    with pytest.raises(ShutdownTransportError):
        listener.start()
    assert not control.exists()
    assert not Path(str(control) + ".tmp").exists()
    listener.stop()
