import pytest
import responses as resp_lib
from src.telegram import TelegramClient


class TestTelegramClient:
    def test_disabled_when_no_token(self):
        tg = TelegramClient(None, None)
        assert not tg.enabled

    def test_disabled_when_no_chat_id(self):
        tg = TelegramClient("token123", None)
        assert not tg.enabled

    def test_enabled_when_both_set(self):
        tg = TelegramClient("token123", "chat456")
        assert tg.enabled

    def test_send_returns_false_when_disabled(self):
        tg = TelegramClient(None, None)
        assert tg.send("hello") is False

    @resp_lib.activate
    def test_send_posts_to_api(self):
        resp_lib.add(resp_lib.POST,
            "https://api.telegram.org/botmy-token/sendMessage",
            json={"ok": True, "result": {}},
            status=200)
        tg = TelegramClient("my-token", "my-chat")
        result = tg.send("hello world")
        assert result is True
        assert len(resp_lib.calls) == 1
        body = resp_lib.calls[0].request.body
        import json
        payload = json.loads(body)
        assert payload["parse_mode"] == "MarkdownV2"
        assert payload["chat_id"] == "my-chat"

    @resp_lib.activate
    def test_send_returns_false_on_http_error(self):
        resp_lib.add(resp_lib.POST,
            "https://api.telegram.org/botmy-token/sendMessage",
            json={"ok": False}, status=400)
        tg = TelegramClient("my-token", "my-chat")
        assert tg.send("hello") is False
