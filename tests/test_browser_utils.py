import importlib
import logging
import os
import stat
import sys
import types
from pathlib import Path

import pytest

from app.services.browser.auth_loader import GeminiAuthStateLoader
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

    monkeypatch.setattr(browser.browser_cookie3, "firefox", lambda **_kwargs: cookies)

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

    monkeypatch.setattr(browser.browser_cookie3, "firefox", lambda **_kwargs: [])

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


@pytest.mark.parametrize("browser_name", ["firefox", "chrome", "brave", "edge"])
def test_browser_cookie3_loaders_are_google_domain_scoped(monkeypatch, browser_name):
    observed = {}

    def load_cookies(**kwargs):
        observed.update(kwargs)
        return []

    monkeypatch.setattr(browser.browser_cookie3, browser_name, load_cookies)

    assert browser.CrossPlatformCookieExtractor()._try_browser_cookie3(browser_name) == []
    assert observed == {"domain_name": "google.com"}


def test_safari_browser_cookie3_call_keeps_existing_signature(monkeypatch):
    observed = {}

    def load_cookies(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return []

    monkeypatch.setattr(browser.browser_cookie3, "safari", load_cookies)

    assert browser.CrossPlatformCookieExtractor()._try_browser_cookie3("safari") == []
    assert observed == {"args": (), "kwargs": {}}


def test_browser_cookie_object_is_canonicalized_with_available_metadata():
    cookie = types.SimpleNamespace(
        name="__Secure-1PSID",
        value="psid",
        domain=".google.com",
        path="/",
        expires=123,
        secure=True,
        _rest={"HTTPOnly": ""},
        partitionKey="https://gemini.google.com",
    )

    assert browser.CrossPlatformCookieExtractor._canonicalize_browser_cookie(cookie) == {
        "name": "__Secure-1PSID",
        "value": "psid",
        "domain": ".google.com",
        "path": "/",
        "expires": 123,
        "secure": True,
        "httpOnly": True,
        "partitionKey": "https://gemini.google.com",
    }


def test_browser_cookie_zero_expiry_is_canonicalized_as_session():
    cookie = types.SimpleNamespace(
        name="__Secure-1PSID",
        value="psid",
        domain=".google.com",
        path="/",
        expires=0,
    )

    canonical = browser.CrossPlatformCookieExtractor._canonicalize_browser_cookie(cookie)

    assert canonical["expires"] is None
    assert GeminiAuthStateLoader.get_browser_webapi_cookie_material([canonical], now=100) == {
        "__Secure-1PSID": "psid"
    }


def test_malformed_browser_cookie_objects_are_skipped():
    extractor = browser.CrossPlatformCookieExtractor()
    valid = types.SimpleNamespace(
        name="__Secure-1PSID",
        value="psid",
        domain=".google.com",
        path="/",
        expires=None,
    )
    malformed_expiry = types.SimpleNamespace(
        name="__Secure-1PSID",
        value="bad",
        domain=".google.com",
        path="/",
        expires="not-a-timestamp",
    )

    assert extractor._canonicalize_browser_cookies([object(), malformed_expiry, valid]) == [
        {
            "name": "__Secure-1PSID",
            "value": "psid",
            "domain": ".google.com",
            "path": "/",
            "expires": None,
        }
    ]


def test_get_cookie_from_browser_rejects_psidts_only(monkeypatch):
    monkeypatch.setitem(browser.CONFIG["Browser"], "name", "firefox")
    cookies = [
        types.SimpleNamespace(
            name="__Secure-1PSIDTS",
            value="psidts",
            domain=".google.com",
            path="/",
            expires=None,
        )
    ]
    monkeypatch.setattr(
        browser.CrossPlatformCookieExtractor,
        "get_cookies_with_fallback",
        lambda _self, _browser_name: cookies,
    )

    assert browser.get_cookie_from_browser("gemini") is None


def test_get_cookie_from_browser_does_not_log_cookie_values(monkeypatch, caplog):
    secret = "browser-cookie-secret"
    monkeypatch.setitem(browser.CONFIG["Browser"], "name", "firefox")
    cookies = [
        types.SimpleNamespace(
            name="__Secure-1PSID",
            value=secret,
            domain=".google.com",
            path="/",
            expires=None,
        )
    ]
    monkeypatch.setattr(
        browser.CrossPlatformCookieExtractor,
        "get_cookies_with_fallback",
        lambda _self, _browser_name: cookies,
    )
    caplog.set_level(logging.INFO)

    assert browser.get_cookie_from_browser("gemini") == {"__Secure-1PSID": secret}
    assert secret not in caplog.text


def test_chromium_direct_query_rejects_non_google_domains(tmp_path):
    cookies_db = tmp_path / "Cookies"
    connection = browser.sqlite3.connect(cookies_db)
    try:
        connection.execute(
            """
            CREATE TABLE cookies (
                name TEXT,
                value TEXT,
                encrypted_value BLOB,
                host_key TEXT,
                path TEXT,
                expires_utc INTEGER,
                is_secure INTEGER,
                is_httponly INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("__Secure-1PSID", "valid", b"", ".google.com", "/", 0, 1, 1),
                ("__Secure-1PSID", "invalid", b"", "evilgoogle.com", "/", 0, 1, 1),
                ("__Secure-1PSIDTS", "invalid", b"", "google.com.evil.example", "/", 0, 1, 1),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    cookies = browser.CrossPlatformCookieExtractor()._get_chromium_cookies_direct(str(cookies_db))

    assert [(cookie.name, cookie.domain) for cookie in cookies] == [
        ("__Secure-1PSID", ".google.com")
    ]


def test_chromium_direct_expiry_matches_unix_seconds_and_handles_session(tmp_path):
    cookies_db = tmp_path / "Cookies"
    expected_expiry = 1_700_000_000
    chromium_expiry = int((expected_expiry + 11_644_473_600) * 1_000_000)
    connection = browser.sqlite3.connect(cookies_db)
    try:
        connection.execute(
            """
            CREATE TABLE cookies (
                name TEXT,
                value TEXT,
                encrypted_value BLOB,
                host_key TEXT,
                path TEXT,
                expires_utc,
                is_secure INTEGER,
                is_httponly INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("__Secure-1PSID", "psid", b"", ".google.com", "/", chromium_expiry, 1, 1),
                ("__Secure-1PSIDTS", "psidts", b"", ".google.com", "/", 0, 1, 1),
                ("__Secure-1PSIDTS", "bad", b"", ".google.com", "/", "invalid", 1, 1),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    cookies = browser.CrossPlatformCookieExtractor()._get_chromium_cookies_direct(str(cookies_db))

    cookies_by_name = {cookie.name: cookie for cookie in cookies}
    assert cookies_by_name["__Secure-1PSID"].expires == pytest.approx(expected_expiry)
    assert cookies_by_name["__Secure-1PSIDTS"].expires is None
    assert len(cookies) == 2

    canonical = browser.CrossPlatformCookieExtractor._canonicalize_browser_cookies(cookies)
    assert GeminiAuthStateLoader.get_browser_webapi_cookie_material(
        canonical, now=expected_expiry + 1
    ) is None
