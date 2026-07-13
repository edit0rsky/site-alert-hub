from __future__ import annotations

import time
from typing import Any

import httpx

from site_alert.config import BotProfile
from site_alert.models import TelegramSendResult
from site_alert.utils.messages import telegram_reply_markup


class TelegramError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable


class TelegramClient:
    def __init__(self, profile: BotProfile, timeout_seconds: float = 20) -> None:
        self._profile = profile
        self._client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def send_message(self, text: str, article_url: str) -> TelegramSendResult:
        endpoint = f"https://api.telegram.org/bot{self._profile.token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self._profile.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": telegram_reply_markup(article_url),
            "disable_web_page_preview": False,
        }
        started = time.perf_counter()
        try:
            response = self._client.post(endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise TelegramError(
                "telegram_timeout", "Telegram request timed out", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise TelegramError(
                "telegram_connection_error", "Telegram request failed", retryable=True
            ) from exc
        _ = time.perf_counter() - started
        if response.status_code in {429, 500, 502, 503, 504}:
            raise TelegramError(
                "telegram_temporary_error",
                f"Telegram returned HTTP {response.status_code}",
                response.status_code,
                retryable=True,
            )
        if response.status_code != 200:
            raise TelegramError(
                "telegram_http_error",
                f"Telegram returned HTTP {response.status_code}",
                response.status_code,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise TelegramError(
                "telegram_invalid_response", "Telegram returned invalid JSON"
            ) from exc
        if not body.get("ok"):
            error_code = str(body.get("error_code", "telegram_api_error"))
            description = str(body.get("description", "Telegram rejected the message"))[:500]
            retryable = error_code == "429" or error_code.startswith("5")
            raise TelegramError(error_code, description, response.status_code, retryable=retryable)
        message = body.get("result", {})
        message_id = message.get("message_id")
        if message_id is None:
            raise TelegramError(
                "telegram_missing_message_id", "Telegram response had no message ID"
            )
        return TelegramSendResult(str(message_id), response.status_code)
