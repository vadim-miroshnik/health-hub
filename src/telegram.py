"""
Отправка сообщений через Telegram Bot API.
Опциональный компонент — если токен не задан, методы — no-op.
"""
import logging
import os
import time

import requests
from dotenv import load_dotenv

if not os.environ.get("NO_DOTENV"):
    load_dotenv(override=True)

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 1.0


class TelegramClient:
    API_BASE = "https://api.telegram.org/bot{token}"

    def __init__(self, token: str | None, chat_id: str | None):
        self.token = token
        self.chat_id = chat_id

    @classmethod
    def from_env(cls) -> "TelegramClient":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None
        return cls(token, chat_id)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        """
        Send a MarkdownV2 message with up to _MAX_ATTEMPTS attempts.

        Retries on any `requests.exceptions.RequestException` (network
        failures, timeouts, 5xx raised via raise_for_status) with exponential
        backoff: 1s, 2s, 4s. 4xx responses (e.g. 400 Bad Request from
        malformed MarkdownV2) are NOT retried — they'd fail identically.

        Returns True if any attempt succeeds; False on final failure or when
        the client is not configured.
        """
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(url, json=payload, timeout=10)
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                self._backoff(attempt)
                continue

            status = resp.status_code
            if 200 <= status < 300:
                return True
            if 400 <= status < 500:
                # 4xx means our message is malformed; retrying won't help.
                logger.error(
                    "Telegram send failed with %d (non-retryable): %s",
                    status, resp.text[:200],
                )
                return False

            # 5xx — worth retrying
            last_exc = requests.exceptions.HTTPError(
                f"HTTP {status}: {resp.text[:200]}"
            )
            self._backoff(attempt)

        logger.error(
            "Telegram send failed after %d attempts: %s",
            _MAX_ATTEMPTS, last_exc,
        )
        return False

    @staticmethod
    def _backoff(attempt: int) -> None:
        if attempt >= _MAX_ATTEMPTS:
            return
        delay = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
        time.sleep(delay)
