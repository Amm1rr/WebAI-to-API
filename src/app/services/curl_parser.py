# src/app/services/curl_parser.py
import re
import shlex
from typing import Optional, Dict


class CurlParseResult:
    """Result of parsing a cURL command or cookie string."""

    def __init__(self):
        self.secure_1psid: Optional[str] = None
        self.secure_1psidts: Optional[str] = None
        self.all_cookies: Dict[str, str] = {}
        self.url: Optional[str] = None
        self.errors: list[str] = []

    @property
    def is_valid(self) -> bool:
        return bool(self.secure_1psid and self.secure_1psidts)


def parse_cookies_from_string(cookie_string: str) -> Dict[str, str]:
    """Parse a semicolon-separated cookie string into a dict."""
    cookies = {}
    for pair in cookie_string.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, _, value = pair.partition("=")
            cookies[name.strip()] = value.strip()
    return cookies


def parse_curl_command(raw_input: str) -> CurlParseResult:
    """
    Parse either a full cURL command or a raw cookie header string.
    Extracts __Secure-1PSID and __Secure-1PSIDTS values.

    Supports:
    - Full cURL from Chrome/Firefox DevTools "Copy as cURL"
    - Raw Cookie header value (semicolon-separated pairs)
    """
    result = CurlParseResult()
    text = raw_input.strip()

    if not text:
        result.errors.append("Empty input")
        return result

    if text.lower().startswith("curl "):
        # Normalize line continuations
        text_clean = text.replace("\\\n", " ").replace("\\\r\n", " ")

        # Try shlex tokenization first
        try:
            tokens = shlex.split(text_clean)
        except ValueError:
            tokens = []
            result.errors.append("shlex parsing failed, using regex fallback")

        # Find cookie header via token pairs (-H 'cookie: ...')
        for i, token in enumerate(tokens):
            if token in ("-H", "--header") and i + 1 < len(tokens):
                header_val = tokens[i + 1]
                if header_val.lower().startswith("cookie:"):
                    cookie_str = header_val[len("cookie:"):].strip()
                    result.all_cookies = parse_cookies_from_string(cookie_str)

        # Regex fallback if token parsing missed the cookie header
        if not result.all_cookies:
            match = re.search(
                r"-H\s+['\"]cookie:\s*([^'\"]+)['\"]",
                text_clean,
                re.IGNORECASE,
            )
            if match:
                result.all_cookies = parse_cookies_from_string(match.group(1))

        # Extract URL
        url_match = re.search(r"curl\s+['\"]?(https?://[^\s'\"]+)", text_clean)
        if url_match:
            result.url = url_match.group(1)
    else:
        # Assume raw cookie string
        result.all_cookies = parse_cookies_from_string(text)

    # Extract target cookies
    result.secure_1psid = result.all_cookies.get("__Secure-1PSID")
    result.secure_1psidts = result.all_cookies.get("__Secure-1PSIDTS")

    if not result.secure_1psid:
        result.errors.append("__Secure-1PSID cookie not found")
    if not result.secure_1psidts:
        result.errors.append("__Secure-1PSIDTS cookie not found")

    return result
