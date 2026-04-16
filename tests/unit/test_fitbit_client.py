"""Юнит-тесты для src/fitbit_client.py."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import responses as resp_lib

from src.fitbit_client import AuthError, FitbitClient

TOKEN_URL = "https://api.fitbit.com/oauth2/token"
API_BASE = "https://api.fitbit.com"


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture
def tokens_file(tmp_path) -> Path:
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({
        "access_token": "valid_access",
        "refresh_token": "valid_refresh",
        "expires_at": time.time() + 3600,
        "user_id": "TESTUSER",
        "scope": "activity heartrate sleep weight nutrition profile",
    }))
    return path


@pytest.fixture
def client(tokens_file) -> FitbitClient:
    return FitbitClient(
        tokens_path=tokens_file,
        client_id="client_id",
        client_secret="client_secret",
    )


# ---------------------------------------------------------------------------
# Успешный запрос
# ---------------------------------------------------------------------------

class TestGet:
    @resp_lib.activate
    def test_successful_get(self, client):
        resp_lib.add(
            resp_lib.GET,
            f"{API_BASE}/1/user/-/activities/date/2026-04-15.json",
            json={"activities": []},
            status=200,
        )
        data = client.get("/1/user/-/activities/date/2026-04-15.json")
        assert data == {"activities": []}

    @resp_lib.activate
    def test_sends_bearer_token(self, client):
        resp_lib.add(
            resp_lib.GET,
            f"{API_BASE}/1/user/-/activities/date/2026-04-15.json",
            json={},
            status=200,
        )
        client.get("/1/user/-/activities/date/2026-04-15.json")
        request = resp_lib.calls[0].request
        assert request.headers["Authorization"] == "Bearer valid_access"

    @resp_lib.activate
    def test_passes_query_params(self, client):
        resp_lib.add(
            resp_lib.GET,
            f"{API_BASE}/1/user/-/foods/log/date/2026-04-15.json",
            json={"foods": []},
            status=200,
        )
        client.get("/1/user/-/foods/log/date/2026-04-15.json", params={"timezone": "UTC"})
        assert "timezone=UTC" in resp_lib.calls[0].request.url


# ---------------------------------------------------------------------------
# Auto-refresh при 401
# ---------------------------------------------------------------------------

class TestAutoRefresh:
    @resp_lib.activate
    def test_refreshes_on_401_and_retries(self, client, tokens_file):
        # Первый запрос → 401
        resp_lib.add(
            resp_lib.GET,
            f"{API_BASE}/1/user/-/sleep/date/2026-04-15.json",
            json={"errors": [{"errorType": "expired_token"}]},
            status=401,
        )
        # Refresh endpoint
        resp_lib.add(
            resp_lib.POST,
            TOKEN_URL,
            json={
                "access_token": "new_access",
                "refresh_token": "new_refresh",
                "expires_in": 28800,
            },
            status=200,
        )
        # Повторный запрос с новым токеном → 200
        resp_lib.add(
            resp_lib.GET,
            f"{API_BASE}/1/user/-/sleep/date/2026-04-15.json",
            json={"sleep": []},
            status=200,
        )

        data = client.get("/1/user/-/sleep/date/2026-04-15.json")
        assert data == {"sleep": []}

        # Токены обновились в памяти и на диске
        assert client._tokens["access_token"] == "new_access"
        saved = json.loads(tokens_file.read_text())
        assert saved["access_token"] == "new_access"
        assert saved["refresh_token"] == "new_refresh"

    @resp_lib.activate
    def test_retry_uses_new_token(self, client):
        resp_lib.add(
            resp_lib.GET,
            f"{API_BASE}/1/user/-/sleep/date/2026-04-15.json",
            status=401,
            json={},
        )
        resp_lib.add(
            resp_lib.POST,
            TOKEN_URL,
            json={"access_token": "refreshed", "refresh_token": "r2", "expires_in": 28800},
            status=200,
        )
        resp_lib.add(
            resp_lib.GET,
            f"{API_BASE}/1/user/-/sleep/date/2026-04-15.json",
            json={"sleep": []},
            status=200,
        )
        client.get("/1/user/-/sleep/date/2026-04-15.json")
        retry_request = resp_lib.calls[2].request
        assert retry_request.headers["Authorization"] == "Bearer refreshed"


# ---------------------------------------------------------------------------
# Невалидный refresh token → SystemExit
# ---------------------------------------------------------------------------

class TestRefreshFailure:
    @resp_lib.activate
    def test_exits_on_invalid_refresh_token(self, client):
        resp_lib.add(
            resp_lib.GET,
            f"{API_BASE}/1/user/-/activities/date/2026-04-15.json",
            status=401,
            json={},
        )
        resp_lib.add(
            resp_lib.POST,
            TOKEN_URL,
            status=401,
            json={"errors": [{"errorType": "invalid_grant"}]},
        )
        from src.fitbit_client import AuthError
        with pytest.raises(AuthError):
            client.get("/1/user/-/activities/date/2026-04-15.json")

    @resp_lib.activate
    def test_sends_telegram_alert_on_auth_failure(self, tokens_file):
        tg_client = FitbitClient(
            tokens_path=tokens_file,
            client_id="cid",
            client_secret="csecret",
            telegram_token="bot_token",
            telegram_chat="12345",
        )
        resp_lib.add(resp_lib.GET, f"{API_BASE}/endpoint", status=401, json={})
        resp_lib.add(resp_lib.POST, TOKEN_URL, status=401, json={"errors": []})
        resp_lib.add(
            resp_lib.POST,
            "https://api.telegram.org/botbot_token/sendMessage",
            json={"ok": True},
            status=200,
        )

        from src.fitbit_client import AuthError
        with pytest.raises(AuthError):
            tg_client.get("/endpoint")

        tg_calls = [
            c for c in resp_lib.calls
            if "telegram.org" in c.request.url
        ]
        assert len(tg_calls) == 1

    @resp_lib.activate
    def test_exits_when_no_refresh_token(self, tmp_path):
        path = tmp_path / "tokens.json"
        path.write_text(json.dumps({
            "access_token": "acc",
            "refresh_token": "",
            "expires_at": time.time() + 3600,
        }))
        client = FitbitClient(path, "cid", "csecret")
        resp_lib.add(resp_lib.GET, f"{API_BASE}/test", status=401, json={})

        from src.fitbit_client import AuthError
        with pytest.raises(AuthError):
            client.get("/test")


# ---------------------------------------------------------------------------
# Загрузка токенов
# ---------------------------------------------------------------------------

class TestTokensFile:
    def test_raises_if_tokens_file_missing(self, tmp_path):
        path = tmp_path / "missing.json"
        with pytest.raises(RuntimeError, match="токенов не найден"):
            FitbitClient(path, "cid", "csecret")

    def test_from_env(self, tokens_file, monkeypatch):
        monkeypatch.setenv("FITBIT_CLIENT_ID", "env_id")
        monkeypatch.setenv("FITBIT_CLIENT_SECRET", "env_secret")
        monkeypatch.setenv("TOKENS_PATH", str(tokens_file))
        client = FitbitClient.from_env()
        assert client._client_id == "env_id"

    def test_from_env_raises_without_credentials(self, monkeypatch):
        monkeypatch.delenv("FITBIT_CLIENT_ID", raising=False)
        monkeypatch.delenv("FITBIT_CLIENT_SECRET", raising=False)
        with pytest.raises(RuntimeError):
            FitbitClient.from_env()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_no_wait_under_limit(self, client):
        """До лимита sleep не вызывается."""
        with patch("time.sleep") as mock_sleep:
            client._rate_wait()
        mock_sleep.assert_not_called()

    def test_waits_when_limit_reached(self, client):
        """При достижении лимита должен вызвать time.sleep."""
        now = time.time()
        # Заполняем очередь до лимита — все запросы в последние 10 секунд
        for i in range(client._rate_limit):
            client._request_times.append(now - 10)

        with patch("time.sleep") as mock_sleep:
            client._rate_wait()

        mock_sleep.assert_called_once()
        sleep_duration = mock_sleep.call_args[0][0]
        assert sleep_duration > 0

    def test_old_requests_evicted(self, client):
        """Запросы старше часа не учитываются в лимите."""
        old = time.time() - 3700  # старше окна
        for _ in range(client._rate_limit):
            client._request_times.append(old)

        with patch("time.sleep") as mock_sleep:
            client._rate_wait()

        mock_sleep.assert_not_called()
