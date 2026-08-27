import os
import stat
from pathlib import Path

from app.utils import browser


def test_chromium_cookie_copy_preserves_destination_mode(monkeypatch, tmp_path):
    source = tmp_path / "Cookies"
    source.write_bytes(b"cookie database bytes")
    if os.name == "posix":
        os.chmod(source, 0o644)

    class Cursor:
        rowcount = 1

        def execute(self, query):
            pass

        def fetchall(self):
            return [
                ("__Secure-1PSID", "cookie-value", b"", ".google.com", "/", 0, 1, 1)
            ]

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    observed = {}

    def connect(path):
        copied = Path(path)
        observed["mode"] = stat.S_IMODE(os.stat(copied).st_mode)
        observed["content"] = copied.read_bytes()
        return Connection()

    monkeypatch.setattr(browser.sqlite3, "connect", connect)

    cookies = browser.CrossPlatformCookieExtractor()._get_chromium_cookies_direct(
        str(source)
    )

    assert observed["content"] == source.read_bytes()
    if os.name == "posix":
        assert observed["mode"] == 0o600
    assert cookies[0].name == "__Secure-1PSID"
    assert cookies[0].value == "cookie-value"
