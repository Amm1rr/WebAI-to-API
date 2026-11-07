# src/app/services/stats_collector.py
import time
import threading
from typing import Optional


class StatsCollector:
    """Thread-safe singleton for tracking request statistics."""

    _instance: Optional["StatsCollector"] = None

    def __init__(self):
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._total_requests = 0
        self._success_count = 0
        self._error_count = 0
        self._endpoint_counts: dict[str, int] = {}
        self._endpoint_success: dict[str, int] = {}
        self._endpoint_error: dict[str, int] = {}
        self._endpoint_last_seen: dict[str, float] = {}
        self._last_request_time: Optional[float] = None
        self._last_error: Optional[dict] = None
        self._recent_errors: list[dict] = []

    @classmethod
    def get_instance(cls) -> "StatsCollector":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def record_request(self, path: str, status_code: int) -> None:
        with self._lock:
            now = time.time()
            self._total_requests += 1
            self._last_request_time = now
            self._endpoint_counts[path] = self._endpoint_counts.get(path, 0) + 1
            self._endpoint_last_seen[path] = now
            is_success = 200 <= status_code < 400
            if is_success:
                self._success_count += 1
                self._endpoint_success[path] = self._endpoint_success.get(path, 0) + 1
            else:
                self._error_count += 1
                self._endpoint_error[path] = self._endpoint_error.get(path, 0) + 1

    def record_error(
        self,
        error_type: str,
        message: str,
        endpoint: str,
        model: str = "",
        status_code: int = 500,
    ) -> dict:
        with self._lock:
            now = time.time()
            msg_lower = message.lower()

            if "stalled" in msg_lower or error_type == "stalled":
                cat = "stalled"
                title = "Google Web Response Stalled"
                suggestion = (
                    "Google Gemini web connection stalled (>60s). The web protocol format may have changed. "
                    "Updating gemini-webapi or pulling the latest WebAI-to-API image is recommended."
                )
            elif "parse" in msg_lower or "unexpected response" in msg_lower or error_type == "parse":
                cat = "parse"
                title = "Response Parsing Error"
                suggestion = (
                    "Failed to parse Google response body. Google may have updated their web RPC format. "
                    "Updating gemini-webapi or pulling the latest image is recommended."
                )
            elif "auth" in msg_lower or "cookie" in msg_lower or error_type == "auth":
                cat = "auth"
                title = "Cookie Session Expired / Invalid"
                suggestion = (
                    "Gemini session cookies (__Secure-1PSID / __Secure-1PSIDTS) are expired or invalid. "
                    "Please re-login to gemini.google.com and import fresh cookies in Configuration tab."
                )
            elif "zombie" in msg_lower or error_type == "zombie":
                cat = "zombie"
                title = "Stream Terminated Unexpectedly"
                suggestion = (
                    "Active stream terminated before completion. If recurring, check proxy or library updates."
                )
            elif "quota" in msg_lower or "rate" in msg_lower or error_type == "quota":
                cat = "quota"
                title = "Quota / Rate Limit Exceeded"
                suggestion = "Account rate limit reached. Wait for reset or switch to a different model."
            elif "network" in msg_lower or "timeout" in msg_lower or error_type == "network":
                cat = "network"
                title = "Network Connection Error"
                suggestion = "Failed to connect to Google servers. Verify internet access or proxy settings."
            else:
                cat = "error"
                title = "Runtime API Error"
                suggestion = "An error occurred during request processing. Check Logs tab for details."

            error_entry = {
                "id": f"err-{int(now * 1000)}",
                "category": cat,
                "title": title,
                "message": message[:300] if len(message) > 300 else message,
                "endpoint": endpoint,
                "model": model,
                "status_code": status_code,
                "timestamp": now,
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "suggestion": suggestion,
            }

            self._last_error = error_entry
            self._recent_errors.insert(0, error_entry)
            if len(self._recent_errors) > 10:
                self._recent_errors = self._recent_errors[:10]

            return error_entry

    def clear_errors(self) -> None:
        with self._lock:
            self._last_error = None
            self._recent_errors.clear()

    def get_last_error(self) -> Optional[dict]:
        with self._lock:
            return self._last_error

    def get_recent_errors(self) -> list[dict]:
        with self._lock:
            return list(self._recent_errors)

    def get_stats(self) -> dict:
        with self._lock:
            uptime_seconds = time.time() - self._start_time
            hours, remainder = divmod(int(uptime_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)

            # Build per-endpoint detail
            endpoints_detail = {}
            for path, count in self._endpoint_counts.items():
                endpoints_detail[path] = {
                    "count": count,
                    "success": self._endpoint_success.get(path, 0),
                    "error": self._endpoint_error.get(path, 0),
                    "last_seen": self._endpoint_last_seen.get(path),
                }

            return {
                "uptime": f"{hours}h {minutes}m {seconds}s",
                "uptime_seconds": uptime_seconds,
                "total_requests": self._total_requests,
                "success_count": self._success_count,
                "error_count": self._error_count,
                "endpoints": {p: d["count"] for p, d in endpoints_detail.items()},
                "endpoints_detail": endpoints_detail,
                "last_request_time": self._last_request_time,
                "last_error": self._last_error,
                "recent_errors": list(self._recent_errors),
            }
