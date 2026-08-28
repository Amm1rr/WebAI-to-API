import importlib
import logging
import os
import stat
import sys
import types
from pathlib import Path

from app.utils import browser


def _crypto_modules():
    win32crypt = types.ModuleType("win32crypt")
    cipher = types.ModuleType("Cryptodome.Cipher")
    cipher.AES = object()
    cryptodome = types.ModuleType("Cryptodome")
    cryptodome.__path__ = []
    cryptodome.Cipher = cipher
    return {
        "win32crypt": win32crypt,
        "Cryptodome": cryptodome,
        "Cryptodome.Cipher": cipher,
    }


def test_windows_crypto_import_uses_pycryptodomex_namespace(monkeypatch):
    try:
        with monkeypatch.context() as patch:
            patch.setattr(browser.platform, "system", lambda: "Windows")
            for name, module in _crypto_modules().items():
                patch.setitem(sys.modules, name, module)

            reloaded = importlib.reload(browser)

            assert reloaded.HAS_CRYPTO is True
            assert reloaded.AES is sys.modules["Cryptodome.Cipher"].AES
    finally:
        importlib.reload(browser)


def test_windows_crypto_import_warning_matches_project_dependency(caplog, monkeypatch):
    caplog.set_level(logging.WARNING)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(browser.platform, "system", lambda: "Windows")
            patch.setitem(sys.modules, "win32crypt", types.ModuleType("win32crypt"))
            patch.setitem(sys.modules, "Cryptodome", None)
            patch.setitem(sys.modules, "Cryptodome.Cipher", None)

            reloaded = importlib.reload(browser)

            assert reloaded.HAS_CRYPTO is False
            assert "Install project dependencies with Poetry." in caplog.text
            assert "pycryptodome" not in caplog.text.lower()
    finally:
        importlib.reload(browser)


def test_non_windows_crypto_import_remains_disabled(monkeypatch):
    try:
        with monkeypatch.context() as patch:
            patch.setattr(browser.platform, "system", lambda: "Linux")

            reloaded = importlib.reload(browser)

            assert reloaded.HAS_CRYPTO is False
    finally:
        importlib.reload(browser)


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
