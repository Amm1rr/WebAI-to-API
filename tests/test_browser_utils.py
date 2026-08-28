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


def test_firefox_cookie3_success_skips_windows_direct_fallback(monkeypatch):
    extractor = browser.CrossPlatformCookieExtractor()
    extractor.is_windows = True
    cookies = [types.SimpleNamespace(name="__Secure-1PSID", value="cookie")]

    monkeypatch.setattr(browser.browser_cookie3, "firefox", lambda: cookies)

    def unexpected_profile_lookup(_browser_name):
        raise AssertionError("Firefox direct fallback should not run")

    monkeypatch.setattr(extractor, "_get_browser_profile_paths", unexpected_profile_lookup)

    assert extractor.get_cookies_with_fallback("firefox") is cookies


def test_firefox_cookie3_failure_returns_none_without_direct_fallback(monkeypatch):
    extractor = browser.CrossPlatformCookieExtractor()
    extractor.is_windows = True

    def fail_firefox():
        raise RuntimeError("cookie database unavailable")

    monkeypatch.setattr(browser.browser_cookie3, "firefox", fail_firefox)

    def unexpected_profile_lookup(_browser_name):
        raise AssertionError("Firefox direct fallback should not run")

    monkeypatch.setattr(extractor, "_get_browser_profile_paths", unexpected_profile_lookup)

    assert extractor.get_cookies_with_fallback("firefox") is None


def test_firefox_cookie3_empty_returns_none_without_direct_fallback(monkeypatch):
    extractor = browser.CrossPlatformCookieExtractor()
    extractor.is_windows = True

    monkeypatch.setattr(browser.browser_cookie3, "firefox", lambda: [])

    def unexpected_profile_lookup(_browser_name):
        raise AssertionError("Firefox direct fallback should not run")

    monkeypatch.setattr(extractor, "_get_browser_profile_paths", unexpected_profile_lookup)

    assert extractor.get_cookies_with_fallback("firefox") is None


def test_get_cookie_from_browser_returns_none_when_firefox_cookie3_fails(monkeypatch):
    monkeypatch.setitem(browser.CONFIG["Browser"], "name", "firefox")

    def fail_firefox():
        raise RuntimeError("cookie database unavailable")

    monkeypatch.setattr(browser.browser_cookie3, "firefox", fail_firefox)

    assert browser.get_cookie_from_browser("gemini") is None


def test_windows_chromium_direct_fallback_remains_available(monkeypatch, tmp_path):
    cookies_db = tmp_path / "Cookies"
    cookies_db.write_bytes(b"database")
    extractor = browser.CrossPlatformCookieExtractor()
    extractor.is_windows = True
    expected = [object()]
    observed = {}

    monkeypatch.setattr(extractor, "_try_browser_cookie3", lambda _browser_name: None)
    monkeypatch.setattr(
        extractor,
        "_get_browser_profile_paths",
        lambda _browser_name: {"cookies_db": str(cookies_db), "local_state": "Local State"},
    )

    def direct_extraction(path, local_state_path):
        observed["path"] = path
        observed["local_state_path"] = local_state_path
        return expected

    monkeypatch.setattr(extractor, "_get_chromium_cookies_direct", direct_extraction)

    assert extractor.get_cookies_with_fallback("chrome") is expected
    assert observed == {
        "path": str(cookies_db),
        "local_state_path": "Local State",
    }
