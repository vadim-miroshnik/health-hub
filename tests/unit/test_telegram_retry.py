"""
P2.2: TelegramClient retries on transient failures (exceptions and 5xx),
with exponential backoff, up to 3 attempts. 4xx failures are non-retryable.
"""

import logging

import pytest
import requests
import responses as resp_lib

from src.telegram import TelegramClient


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Don't actually sleep during backoff in tests."""
    import src.telegram as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


class TestRetryExceptionPath:
    @resp_lib.activate
    def test_two_connection_errors_then_success(self):
        url = "https://api.telegram.org/botT/sendMessage"
        resp_lib.add(resp_lib.POST, url, body=requests.exceptions.ConnectionError("down"))
        resp_lib.add(resp_lib.POST, url, body=requests.exceptions.ConnectionError("still"))
        resp_lib.add(resp_lib.POST, url, json={"ok": True}, status=200)

        tg = TelegramClient("T", "C")
        assert tg.send("hi") is True
        assert len(resp_lib.calls) == 3

    @resp_lib.activate
    def test_all_attempts_fail_returns_false(self, caplog):
        url = "https://api.telegram.org/botT/sendMessage"
        for _ in range(3):
            resp_lib.add(resp_lib.POST, url, body=requests.exceptions.ConnectionError("down"))

        tg = TelegramClient("T", "C")
        with caplog.at_level(logging.ERROR, logger="src.telegram"):
            assert tg.send("hi") is False
        assert len(resp_lib.calls) == 3
        assert any("after 3 attempts" in r.message for r in caplog.records)


class TestRetry5xxPath:
    @resp_lib.activate
    def test_5xx_retries(self):
        url = "https://api.telegram.org/botT/sendMessage"
        resp_lib.add(resp_lib.POST, url, json={"ok": False}, status=502)
        resp_lib.add(resp_lib.POST, url, json={"ok": False}, status=503)
        resp_lib.add(resp_lib.POST, url, json={"ok": True}, status=200)

        tg = TelegramClient("T", "C")
        assert tg.send("hi") is True
        assert len(resp_lib.calls) == 3


class TestNoRetryOn4xx:
    @resp_lib.activate
    def test_400_does_not_retry(self, caplog):
        url = "https://api.telegram.org/botT/sendMessage"
        resp_lib.add(resp_lib.POST, url, json={"ok": False}, status=400)

        tg = TelegramClient("T", "C")
        with caplog.at_level(logging.ERROR, logger="src.telegram"):
            assert tg.send("hi") is False
        # Only ONE request — 4xx aborts the retry loop
        assert len(resp_lib.calls) == 1
        assert any("non-retryable" in r.message for r in caplog.records)

    @resp_lib.activate
    def test_429_does_not_retry(self):
        # Telegram rate-limit: also non-retryable at this layer (caller
        # handles scheduling). 4xx path covers it.
        url = "https://api.telegram.org/botT/sendMessage"
        resp_lib.add(resp_lib.POST, url, json={"ok": False}, status=429)
        tg = TelegramClient("T", "C")
        assert tg.send("hi") is False
        assert len(resp_lib.calls) == 1


class TestBackoffSchedule:
    def test_backoff_is_exponential(self, monkeypatch):
        """Attempts 1, 2 should sleep 1s, 2s; no sleep after attempt 3."""
        sleeps: list[float] = []
        import src.telegram as mod
        monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))

        with resp_lib.RequestsMock() as rsps:
            url = "https://api.telegram.org/botT/sendMessage"
            for _ in range(3):
                rsps.add(rsps.POST, url, body=requests.exceptions.ConnectionError("x"))
            TelegramClient("T", "C").send("hi")
        assert sleeps == [1.0, 2.0]
