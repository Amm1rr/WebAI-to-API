import io

from app.utils import startup


def test_startup_output_reconfigures_redirected_streams_and_preserves_symbols(
    monkeypatch,
):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(startup.sys, "stdout", stdout)
    monkeypatch.setattr(startup.sys, "stderr", stderr)

    startup.configure_startup_output()
    startup.print_gemini_preflight_status(True)
    startup.print_server_info("127.0.0.1", 6969, "webai")
    print("⚠️", file=stderr)
    stdout.flush()
    stderr.flush()

    assert stdout.encoding.lower() == "utf-8"
    assert stderr.encoding.lower() == "utf-8"
    assert stdout.errors == "replace"
    assert stderr.errors == "replace"
    assert "✅ Gemini service is enabled" in stdout.buffer.getvalue().decode()
    assert "✨ Available Services:" in stdout.buffer.getvalue().decode()
    assert "⚠️" in stderr.buffer.getvalue().decode()


def test_startup_primary_apis_lists_stateless_not_temporary(monkeypatch):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(startup.sys, "stdout", stdout)
    monkeypatch.setattr(startup.sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))

    startup.print_server_info("127.0.0.1", 6969, "webai")
    stdout.flush()
    output = stdout.buffer.getvalue().decode()

    # Extract Primary APIs section
    primary_section = output.split("🔗 Primary APIs:")[1].split("🔗 Useful Endpoints:")[0] if "🔗 Primary APIs:" in output else output
    assert "/v1/stateless/chat/completions" in primary_section
    assert "/v1/temporary/chat/completions" not in primary_section
    assert "/v1/chat/completions" in primary_section


def test_startup_output_ignores_streams_without_reconfigure(monkeypatch):
    class PlainStream:
        def __init__(self):
            self.writes = []

        def write(self, text):
            self.writes.append(text)

        def flush(self):
            pass

    stdout = PlainStream()
    stderr = PlainStream()
    monkeypatch.setattr(startup.sys, "stdout", stdout)
    monkeypatch.setattr(startup.sys, "stderr", stderr)

    startup.configure_startup_output()
    startup.print_gemini_preflight_status(True)

    assert any("✅ Gemini service is enabled" in text for text in stdout.writes)
