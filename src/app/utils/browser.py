# src/app/utils/browser.py
import logging
import browser_cookie3
import platform
import os
import sqlite3
import json
import base64
import math
from pathlib import Path
from typing import Optional, Literal, Dict, Any
from app.config import CONFIG
from app.services.browser.auth_loader import GeminiAuthStateLoader

# Windows-specific imports for cookie decryption
if platform.system().lower() == "windows":
    try:
        import win32crypt
        from Cryptodome.Cipher import AES
        HAS_CRYPTO = True
    except ImportError:
        HAS_CRYPTO = False
        logging.warning("Windows crypto libraries not available. Install project dependencies with Poetry.")
else:
    HAS_CRYPTO = False

logger = logging.getLogger(__name__)

class CrossPlatformCookieExtractor:
    """Cross-platform cookie extractor with Windows compatibility fixes"""
    
    def __init__(self):
        self.system = platform.system().lower()
        self.is_windows = self.system == "windows"
        logger.info(f"Initialized cookie extractor for {self.system}")
    
    def _get_browser_profile_paths(self, browser_name: str) -> Dict[str, Any]:
        """Get browser profile paths for different operating systems"""
        paths = {}
        
        if self.is_windows:
            user_data = os.path.expanduser("~")
            if browser_name == "chrome":
                base_path = os.path.join(user_data, "AppData", "Local", "Google", "Chrome", "User Data")
                # Check multiple possible locations for Chrome cookies
                possible_paths = [
                    os.path.join(base_path, "Default", "Network", "Cookies"),  # New Chrome location
                    os.path.join(base_path, "Default", "Cookies"),  # Old Chrome location
                ]
                cookies_path = None
                for path in possible_paths:
                    if os.path.exists(path):
                        cookies_path = path
                        logger.info(f"Found Chrome cookies at: {path}")
                        break
                
                paths = {
                    "cookies_db": cookies_path,
                    "local_state": os.path.join(base_path, "Local State")
                }
                
            elif browser_name == "brave":
                base_path = os.path.join(user_data, "AppData", "Local", "BraveSoftware", "Brave-Browser", "User Data")
                # Check multiple possible locations for Brave cookies
                possible_paths = [
                    os.path.join(base_path, "Default", "Network", "Cookies"),  # New Brave location
                    os.path.join(base_path, "Default", "Cookies"),  # Old Brave location
                ]
                cookies_path = None
                for path in possible_paths:
                    if os.path.exists(path):
                        cookies_path = path
                        logger.info(f"Found Brave cookies at: {path}")
                        break
                
                paths = {
                    "cookies_db": cookies_path,
                    "local_state": os.path.join(base_path, "Local State")
                }
                
            elif browser_name == "edge":
                base_path = os.path.join(user_data, "AppData", "Local", "Microsoft", "Edge", "User Data")
                # Check multiple possible locations for Edge cookies
                possible_paths = [
                    os.path.join(base_path, "Default", "Network", "Cookies"),  # New Edge location
                    os.path.join(base_path, "Default", "Cookies"),  # Old Edge location
                ]
                cookies_path = None
                for path in possible_paths:
                    if os.path.exists(path):
                        cookies_path = path
                        logger.info(f"Found Edge cookies at: {path}")
                        break
                
                paths = {
                    "cookies_db": cookies_path,
                    "local_state": os.path.join(base_path, "Local State")
                }
                

        return paths
    
    def _try_browser_cookie3(self, browser_name: str) -> Optional[Any]:
        """Try to get cookies using browser_cookie3 library"""
        try:
            if browser_name == "firefox":
                return browser_cookie3.firefox(domain_name="google.com")
            elif browser_name == "chrome":
                return browser_cookie3.chrome(domain_name="google.com")
            elif browser_name == "brave":
                return browser_cookie3.brave(domain_name="google.com")
            elif browser_name == "edge":
                return browser_cookie3.edge(domain_name="google.com")
            elif browser_name == "safari":
                return browser_cookie3.safari()
            else:
                raise ValueError(f"Unsupported browser: {browser_name}")
        except Exception as e:
            logger.warning(f"browser_cookie3 failed for {browser_name}: {e}")
            return None

    @staticmethod
    def _canonicalize_browser_cookie(cookie: Any) -> Optional[Dict[str, Any]]:
        """Convert a browser cookie object into canonical storage-state fields."""
        missing = object()

        try:
            name = cookie.name
            value = cookie.value
            domain = cookie.domain
            path = getattr(cookie, "path", "/") or "/"
        except Exception:
            return None

        if not all(isinstance(item, str) for item in (name, value, domain, path)):
            return None

        canonical: Dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
        }

        try:
            if hasattr(cookie, "expires"):
                expires = cookie.expires
                if expires is not None:
                    if (
                        isinstance(expires, bool)
                        or not isinstance(expires, (int, float))
                        or not math.isfinite(expires)
                        or expires < 0
                    ):
                        return None
                    canonical["expires"] = None if expires == 0 else expires
                else:
                    canonical["expires"] = None

            secure = getattr(cookie, "secure", missing)
            if secure is not missing:
                canonical["secure"] = bool(secure)

            rest = getattr(cookie, "_rest", None)
            http_only = getattr(cookie, "httponly", missing)
            if http_only is not missing:
                canonical["httpOnly"] = bool(http_only)
            elif isinstance(rest, dict):
                canonical["httpOnly"] = any(
                    str(key).lower() == "httponly" for key in rest
                )

            partition_key = missing
            for attribute in ("partitionKey", "partition_key"):
                partition_key = getattr(cookie, attribute, missing)
                if partition_key is not missing:
                    break

            if partition_key is not missing:
                canonical["partitionKey"] = partition_key
            elif isinstance(rest, dict) and any(
                str(key).lower() == "partitioned" for key in rest
            ):
                canonical["partitionKey"] = True
        except Exception:
            return None

        return canonical

    @classmethod
    def _canonicalize_browser_cookies(cls, cookies: Any) -> list[Dict[str, Any]]:
        """Convert iterable browser cookies, skipping malformed entries."""
        canonical = []
        try:
            for cookie in cookies:
                normalized = cls._canonicalize_browser_cookie(cookie)
                if normalized is not None:
                    canonical.append(normalized)
        except Exception:
            return canonical
        return canonical
    
    def _decrypt_chrome_cookie_value(self, encrypted_value: bytes, local_state_path: str) -> Optional[str]:
        """Decrypt Chrome cookie value on Windows"""
        if not self.is_windows or not HAS_CRYPTO:
            logger.warning("Decryption not available: not Windows or crypto libraries missing")
            return None
            
        try:
            logger.info(f"Attempting decryption with Local State: {local_state_path}")
            logger.info(f"Encrypted value length: {len(encrypted_value)}")
            
            # Read the local state file to get the encryption key
            if not os.path.exists(local_state_path):
                logger.warning(f"Local State file not found: {local_state_path}")
                return None
                
            with open(local_state_path, 'r', encoding='utf-8') as f:
                local_state = json.load(f)
            
            # Get the encrypted key
            if 'os_crypt' not in local_state or 'encrypted_key' not in local_state['os_crypt']:
                logger.warning("os_crypt.encrypted_key not found in Local State")
                return None
                
            encrypted_key = base64.b64decode(local_state['os_crypt']['encrypted_key'])
            logger.info(f"Encrypted key length: {len(encrypted_key)}")
            
            # Remove the 'DPAPI' prefix (first 5 bytes)
            encrypted_key = encrypted_key[5:]
            
            # Decrypt the key using Windows DPAPI
            key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
            logger.info(f"Decrypted key length: {len(key)}")
            
            # The cookie value format: version (3 bytes) + nonce (12 bytes) + encrypted_data + tag (16 bytes)
            if len(encrypted_value) < 31:  # 3 + 12 + 1 + 16 = minimum length
                logger.warning(f"Encrypted value too short: {len(encrypted_value)} bytes")
                return None
                
            # Extract components
            version = encrypted_value[:3]
            logger.info(f"Cookie encryption version: {version}")
            
            if version != b'v10' and version != b'v11':
                # Try old DPAPI method for older Chrome versions
                logger.info("Trying DPAPI decryption for older Chrome")
                try:
                    decrypted = win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1]
                    result = decrypted.decode('utf-8')
                    logger.info(f"DPAPI decryption successful, result length: {len(result)}")
                    return result
                except Exception as e:
                    logger.warning(f"DPAPI decryption failed: {e}")
                    return None
            
            nonce = encrypted_value[3:15]
            ciphertext = encrypted_value[15:-16]
            tag = encrypted_value[-16:]
            
            logger.info(f"AES-GCM components - nonce: {len(nonce)}, ciphertext: {len(ciphertext)}, tag: {len(tag)}")
            
            # Decrypt using AES-GCM
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            decrypted = cipher.decrypt_and_verify(ciphertext, tag)
            
            result = decrypted.decode('utf-8')
            logger.info(f"AES-GCM decryption successful, result length: {len(result)}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to decrypt Chrome cookie: {e}", exc_info=True)
            return None
    
    def _get_chromium_cookies_direct(self, cookies_db_path: str, local_state_path: str = None) -> Optional[list]:
        """Direct Chromium-based browser cookie extraction with decryption support"""
        try:
            if not os.path.exists(cookies_db_path):
                logger.warning(f"Chromium cookies database not found: {cookies_db_path}")
                return None
            
            # Copy the database to avoid lock issues
            import tempfile
            import shutil
            
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as temp_file:
                temp_db_path = temp_file.name
                shutil.copyfile(cookies_db_path, temp_db_path)
            
            try:
                conn = sqlite3.connect(temp_db_path)
                cursor = conn.cursor()
                
                # Chromium cookie table structure - get encrypted_value too
                cursor.execute("""
                    SELECT name, value, encrypted_value, host_key, path, expires_utc, is_secure, is_httponly 
                    FROM cookies 
                    WHERE (
                        host_key = 'google.com'
                        OR host_key = '.google.com'
                        OR host_key LIKE '%.google.com'
                    )
                    AND (name = '__Secure-1PSID' OR name = '__Secure-1PSIDTS')
                """)
                
                logger.info(f"Found {cursor.rowcount} matching cookies in database")
                
                cookies = []
                for row in cursor.fetchall():
                    name, value, encrypted_value, host_key, path, expires_utc, is_secure, is_httponly = row

                    try:
                        if isinstance(expires_utc, bool):
                            raise ValueError
                        if expires_utc == 0:
                            expires = None
                        else:
                            expires_utc = float(expires_utc)
                            if not math.isfinite(expires_utc) or expires_utc <= 0:
                                raise ValueError
                            expires = expires_utc / 1_000_000 - 11_644_473_600
                            if expires <= 0:
                                logger.warning("Skipping expired Chromium cookie")
                                continue
                    except (TypeError, ValueError):
                        logger.warning("Skipping Chromium cookie with invalid expiry")
                        continue
                    
                    logger.info(f"Processing cookie: {name}")
                    logger.info(f"  - Plain value length: {len(value) if value else 0}")
                    logger.info(f"  - Encrypted value length: {len(encrypted_value) if encrypted_value else 0}")
                    logger.info(f"  - Host: {host_key}")
                    
                    # Try to decrypt the cookie value if it's encrypted
                    final_value = value
                    if not value and encrypted_value and self.is_windows and local_state_path:
                        logger.info(f"  - Attempting to decrypt {name}")
                        decrypted_value = self._decrypt_chrome_cookie_value(encrypted_value, local_state_path)
                        if decrypted_value:
                            final_value = decrypted_value
                            logger.info(f"  - Successfully decrypted cookie: {name} (length: {len(final_value)})")
                        else:
                            logger.warning(f"  - Failed to decrypt cookie: {name}")
                    elif value:
                        logger.info(f"  - Using plain text value for {name}")
                    else:
                        logger.warning(f"  - No value found for {name} (neither plain nor encrypted)")
                    
                    cookie_obj = type('Cookie', (), {
                        'name': name,
                        'value': final_value or '',
                        'domain': host_key,
                        'path': path,
                        'expires': expires,
                        'secure': bool(is_secure),
                        'httponly': bool(is_httponly)
                    })()
                    cookies.append(cookie_obj)
                
                conn.close()
                return cookies
            finally:
                # Clean up temp file
                try:
                    os.unlink(temp_db_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"Failed to extract Chromium cookies directly: {e}")
            return None
    
    def get_cookies_with_fallback(self, browser_name: str) -> Optional[Any]:
        """Get cookies with multiple fallback methods"""
        logger.info(f"Attempting to get cookies from {browser_name} with fallback methods")
        
        # Method 1: Try browser_cookie3 first (works well on Linux)
        cookies = self._try_browser_cookie3(browser_name)
        if cookies:
            logger.info(f"Successfully retrieved cookies using browser_cookie3 for {browser_name}")
            return cookies
        
        # Method 2: Try direct Chromium database access (fallback for Windows)
        if self.is_windows and browser_name in ["chrome", "brave", "edge"]:
            logger.info(f"Trying direct database access for {browser_name} on Windows")
            
            browser_paths = self._get_browser_profile_paths(browser_name)

            if "cookies_db" in browser_paths:
                cookies_db_path = browser_paths["cookies_db"]
                local_state_path = browser_paths.get("local_state")
                
                if cookies_db_path and os.path.exists(cookies_db_path):
                    cookies = self._get_chromium_cookies_direct(cookies_db_path, local_state_path)
                    if cookies:
                        logger.info(f"Successfully retrieved {browser_name} cookies via direct access")
                        return cookies
                else:
                    logger.warning(f"Cookies database not found for {browser_name} at expected locations")
        
        logger.warning(f"All cookie extraction methods failed for {browser_name}")
        return None


def get_cookie_from_browser(service: Literal["gemini"]) -> Optional[dict]:
    """Enhanced cookie extraction with cross-platform support"""
    browser_name = CONFIG["Browser"].get("name", "firefox").lower()
    logger.info(f"Attempting to get cookies from browser: {browser_name} for service: {service}")

    extractor = CrossPlatformCookieExtractor()

    try:
        cookies = extractor.get_cookies_with_fallback(browser_name)

        if not cookies:
            logger.error(f"Failed to retrieve cookies from {browser_name}")
            return None

        logger.info(f"Successfully retrieved cookies from {browser_name}")

    except Exception as e:
        logger.error(f"An unexpected error occurred while retrieving cookies from {browser_name}: {e}", exc_info=True)
        return None

    # Process cookies for the requested service
    if service == "gemini":
        logger.info("Filtering cookies for Google domains...")
        canonical_cookies = extractor._canonicalize_browser_cookies(cookies)
        google_cookies = GeminiAuthStateLoader.get_browser_webapi_cookie_material(
            canonical_cookies
        )

        if google_cookies is None:
            logger.warning("No essential Gemini cookies found in browser jar.")
            return None

        logger.info(f"Found {len(google_cookies)} essential Gemini cookies ({list(google_cookies.keys())})")
        return google_cookies
    else:
        logger.warning(f"Unsupported service: {service}")
        return None
