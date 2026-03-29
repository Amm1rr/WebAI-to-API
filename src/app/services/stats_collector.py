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
            }
