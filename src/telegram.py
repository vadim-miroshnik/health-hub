"""
Отправка сообщений через Telegram Bot API.
Опциональный компонент — если токен не задан, методы — no-op.
"""
import os

import requests
from dotenv import load_dotenv

if not os.environ.get("NO_DOTENV"):
    load_dotenv(override=True)


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
        Send MarkdownV2 message. Returns True on success, False on failure.
        Silent no-op if not enabled.
        """
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        try:
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            print(f"WARNING: Telegram send failed: {exc}", file=__import__("sys").stderr)
            return False
        return True
