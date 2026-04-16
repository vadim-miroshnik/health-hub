"""
Fitbit API client с OAuth2 auto-refresh и rate limiting.

Использование:
    from src.fitbit_client import FitbitClient
    client = FitbitClient.from_env()
    data = client.get("/1/user/-/activities/date/2026-04-15.json")
"""

import json
import os
import time
from collections import deque
from pathlib import Path

import requests
from dotenv import load_dotenv

if not os.environ.get("NO_DOTENV"):
    load_dotenv(override=True)

TOKEN_URL = "https://api.fitbit.com/oauth2/token"
API_BASE = "https://api.fitbit.com"

# Personal app: 150 запросов/час
_RATE_LIMIT = 150
_RATE_WINDOW = 3600  # секунды


class AuthError(Exception):
    """Невозможно обновить токен — требуется повторная авторизация."""


class FitbitClient:
    """
    HTTP-клиент для Fitbit API.

    Params:
        tokens_path    — путь к tokens.json
        client_id      — Fitbit app client_id
        client_secret  — Fitbit app client_secret
        telegram_token — Bot API token для алертов (опционально)
        telegram_chat  — chat_id для алертов (опционально)
        rate_limit     — макс. запросов в окне (по умолчанию 150/час)
    """

    def __init__(
        self,
        tokens_path: Path,
        client_id: str,
        client_secret: str,
        telegram_token: str | None = None,
        telegram_chat: str | None = None,
        rate_limit: int = _RATE_LIMIT,
    ) -> None:
        self._tokens_path = tokens_path
        self._client_id = client_id
        self._client_secret = client_secret
        self._telegram_token = telegram_token
        self._telegram_chat = telegram_chat
        self._rate_limit = rate_limit
        self._request_times: deque[float] = deque()
        self._tokens = self._load_tokens()

    # ------------------------------------------------------------------
    # Фабрика из переменных окружения
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "FitbitClient":
        client_id = os.environ.get("FITBIT_CLIENT_ID", "").strip()
        client_secret = os.environ.get("FITBIT_CLIENT_SECRET", "").strip()
        tokens_path = Path(os.environ.get("TOKENS_PATH", "tokens.json"))
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
        telegram_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None

        if not client_id or not client_secret:
            raise RuntimeError(
                "Не заданы FITBIT_CLIENT_ID / FITBIT_CLIENT_SECRET в .env"
            )
        return cls(
            tokens_path=tokens_path,
            client_id=client_id,
            client_secret=client_secret,
            telegram_token=telegram_token,
            telegram_chat=telegram_chat,
        )

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def get(self, endpoint: str, *, params: dict | None = None) -> dict:
        """
        GET-запрос к Fitbit API.

        endpoint — путь вида "/1/user/-/activities/date/2026-04-15.json"
        Возвращает распарсенный JSON.
        Автоматически обновляет токен при 401 и повторяет запрос.
        При 429 ждёт Retry-After секунд и повторяет один раз.
        """
        self._rate_wait()
        resp = self._do_get(endpoint, params)

        if resp.status_code == 401:
            self._refresh()
            self._rate_wait()
            resp = self._do_get(endpoint, params)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            print(f"  Rate limited (429): waiting {retry_after}s...")
            time.sleep(retry_after)
            self._rate_wait()
            resp = self._do_get(endpoint, params)

        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Приватные методы
    # ------------------------------------------------------------------

    def _do_get(self, endpoint: str, params: dict | None) -> requests.Response:
        self._record_request()
        return requests.get(
            f"{API_BASE}{endpoint}",
            headers={"Authorization": f"Bearer {self._tokens['access_token']}"},
            params=params,
            timeout=30,
        )

    def _refresh(self) -> None:
        """Обновляет access_token через refresh_token. При ошибке — alert + exit."""
        refresh_token = self._tokens.get("refresh_token", "")
        if not refresh_token:
            self._fatal_auth("refresh_token отсутствует в tokens.json")

        resp = requests.post(
            TOKEN_URL,
            auth=(self._client_id, self._client_secret),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=30,
        )

        if resp.status_code in (400, 401):
            body = resp.json()
            self._fatal_auth(
                f"Refresh token невалиден: {body.get('errors', resp.text)}"
            )

        resp.raise_for_status()
        raw = resp.json()

        self._tokens["access_token"] = raw["access_token"]
        self._tokens["refresh_token"] = raw["refresh_token"]
        self._tokens["expires_at"] = time.time() + int(raw.get("expires_in", 28800))
        self._save_tokens()

    def _fatal_auth(self, reason: str) -> None:
        """Отправляет алерт в Telegram и бросает AuthError."""
        msg = f"[health-hub] Ошибка авторизации Fitbit: {reason}"
        print(f"ERROR: {msg}")
        if self._telegram_token and self._telegram_chat:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{self._telegram_token}/sendMessage",
                    json={"chat_id": self._telegram_chat, "text": msg},
                    timeout=10,
                )
            except Exception:
                pass  # не маскируем основную ошибку
        raise AuthError(reason)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _rate_wait(self) -> None:
        """Ждёт если в последний час уже отправлено _rate_limit запросов."""
        now = time.time()
        window_start = now - _RATE_WINDOW

        # Убираем устаревшие метки
        while self._request_times and self._request_times[0] < window_start:
            self._request_times.popleft()

        if len(self._request_times) >= self._rate_limit:
            sleep_for = _RATE_WINDOW - (now - self._request_times[0]) + 1
            if sleep_for > 0:
                print(f"  Rate limit: жду {sleep_for:.0f}с...")
                time.sleep(sleep_for)

    def _record_request(self) -> None:
        self._request_times.append(time.time())

    # ------------------------------------------------------------------
    # Токены
    # ------------------------------------------------------------------

    def _load_tokens(self) -> dict:
        if not self._tokens_path.exists():
            raise RuntimeError(
                f"Файл токенов не найден: {self._tokens_path}\n"
                "Запустите: make auth"
            )
        with self._tokens_path.open() as f:
            return json.load(f)

    def _save_tokens(self) -> None:
        tmp = self._tokens_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._tokens, indent=2))
        tmp.rename(self._tokens_path)
