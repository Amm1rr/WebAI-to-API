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


def test_startup_useful_endpoints_lists_stateless_models_not_in_primary(monkeypatch):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(startup.sys, "stdout", stdout)
    monkeypatch.setattr(startup.sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))

    startup.print_server_info("127.0.0.1", 6969, "webai")
    stdout.flush()
    output = stdout.buffer.getvalue().decode()

    assert "🔗 Useful Endpoints:" in output
    assert "🔗 Primary APIs:" in output
    primary_section, useful_section = output.split("🔗 Useful Endpoints:")[0], output.split("🔗 Useful Endpoints:")[1]
    # Primary section is between Primary header and Useful header
    primary_section = primary_section.split("🔗 Primary APIs:")[1] if "🔗 Primary APIs:" in primary_section else primary_section
    # Useful section is after Useful header (up to next separator)
    # Stateless chat remains in Primary
    assert "/v1/stateless/chat/completions" in primary_section
    assert "/v1/stateless/chat/completions" not in useful_section
    # Stateless models appears in Useful, not in Primary, with correct order
    assert "/v1/stateless/models" in useful_section
    assert "/v1/stateless/models" not in primary_section
    # Order: /v1/models → /v1/stateless/models → /v1/auth/status → /v1/auth/login
    idx_models = useful_section.find("/v1/models")
    idx_stateless_models = useful_section.find("/v1/stateless/models")
    idx_auth_status = useful_section.find("/v1/auth/status")
    idx_auth_login = useful_section.find("/v1/auth/login")
    assert idx_models != -1 and idx_stateless_models != -1 and idx_auth_status != -1 and idx_auth_login != -1
    assert idx_models < idx_stateless_models < idx_auth_status < idx_auth_login


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
