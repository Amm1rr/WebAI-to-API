# src/app/services/telegram_notifier.py
"""
Telegram notification service for API error alerts.

Sends Telegram messages when critical API errors occur (auth failures,
service errors). Uses a per-error-type cooldown to prevent notification spam.
"""
import time
from typing import Optional

import httpx

from app.config import CONFIG
from app.logger import logger

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Minimum seconds between notifications of the same error type
_DEFAULT_COOLDOWN = 60

# Only send notifications for these error types by default (auth = cookie expired)
_DEFAULT_NOTIFY_TYPES = "auth"


class TelegramNotifier:
    """Singleton Telegram alert service."""

    _instance: Optional["TelegramNotifier"] = None

    def __init__(self) -> None:
        # Last send time per error category (e.g. "auth", "500", "503")
        self._last_sent: dict[str, float] = {}

    @classmethod
    def get_instance(cls) -> "TelegramNotifier":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cfg() -> dict:
        section = CONFIG["Telegram"] if "Telegram" in CONFIG else {}
        raw_types = section.get("notify_types", _DEFAULT_NOTIFY_TYPES).strip()
        notify_types = {t.strip() for t in raw_types.split(",") if t.strip()}
        return {
            "enabled": str(section.get("enabled", "false")).lower() == "true",
            "bot_token": section.get("bot_token", "").strip(),
            "chat_id": section.get("chat_id", "").strip(),
            "cooldown": int(section.get("cooldown_seconds", _DEFAULT_COOLDOWN)),
            "notify_types": notify_types,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def notify_error(
        self,
        error_type: str,
        message: str,
        endpoint: str = "",
        detail: str = "",
    ) -> bool:
        """
        Send an error notification to Telegram.

        Parameters
        ----------
        error_type:
            Short identifier used for cooldown tracking, e.g. "auth", "500", "503".
        message:
            Human-readable summary shown in the notification title.
        endpoint:
            API path where the error occurred (optional).
        detail:
            Additional error detail / exception message (optional).

        Returns True if a message was sent, False if skipped or failed.
        """
        cfg = self._cfg()
        if not cfg["enabled"]:
            return False
        if not cfg["bot_token"] or not cfg["chat_id"]:
            return False

        # Type filter — only send for configured error types
        if error_type not in cfg["notify_types"]:
            return False

        # Cooldown guard
        now = time.monotonic()
        last = self._last_sent.get(error_type, 0.0)
        if now - last < cfg["cooldown"]:
            return False  # Too soon — skip

        text = self._build_message(error_type, message, endpoint, detail)
        sent = await self._send(cfg["bot_token"], cfg["chat_id"], text)
        if sent:
            self._last_sent[error_type] = now
        return sent

    async def send_test(self, bot_token: str, chat_id: str) -> tuple[bool, str]:
        """Send a test message using the provided credentials."""
        text = (
            "✅ *WebAI-to-API* — Telegram notifications configured successfully!\n"
            "You will receive alerts here when API errors occur."
        )
        ok = await self._send(bot_token, chat_id, text)
        return ok, "Message sent successfully." if ok else "Failed to send message — check token and chat_id."

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_message(error_type: str, message: str, endpoint: str, detail: str) -> str:
        icon = {
            "auth": "🔐",
            "503": "⚠️",
            "500": "🔴",
        }.get(error_type, "❗")

        lines = [f"{icon} *WebAI-to-API Error*", f"*Type:* {error_type.upper()}  |  {message}"]
        if endpoint:
            lines.append(f"*Endpoint:* `{endpoint}`")
        if detail:
            # Truncate long details
            truncated = detail[:300] + ("…" if len(detail) > 300 else "")
            lines.append(f"*Detail:* {truncated}")
        return "\n".join(lines)

    @staticmethod
    async def _send(bot_token: str, chat_id: str, text: str) -> bool:
        url = _TELEGRAM_API.format(token=bot_token)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                })
                if resp.status_code == 200 and resp.json().get("ok"):
                    return True
                logger.warning(f"[TelegramNotifier] Send failed: {resp.status_code} {resp.text[:200]}")
                return False
        except Exception as exc:
            logger.warning(f"[TelegramNotifier] Exception sending message: {exc}")
            return False
