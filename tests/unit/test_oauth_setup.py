"""Юнит-тесты для auth/oauth_setup.py."""

import base64
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from auth.oauth_setup import (
    _code_challenge,
    _generate_code_verifier,
    _save_tokens,
)


class TestPKCE:
    def test_verifier_is_base64url(self):
        verifier = _generate_code_verifier()
        # Должен содержать только символы base64url (без padding)
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert set(verifier) <= allowed

    def test_verifier_length(self):
        verifier = _generate_code_verifier()
        # 96 байт → base64url без паддинга = 128 символов
        assert len(verifier) == 128

    def test_verifier_is_random(self):
        v1 = _generate_code_verifier()
        v2 = _generate_code_verifier()
        assert v1 != v2

    def test_code_challenge_is_sha256_base64url(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        challenge = _code_challenge(verifier)
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert challenge == expected

    def test_code_challenge_no_padding(self):
        verifier = _generate_code_verifier()
        challenge = _code_challenge(verifier)
        assert "=" not in challenge


class TestSaveTokens:
    def test_saves_required_fields(self, tmp_path):
        path = tmp_path / "tokens.json"
        raw = {
            "access_token": "acc123",
            "refresh_token": "ref456",
            "expires_in": 28800,
            "user_id": "ABC",
            "scope": "activity sleep",
        }
        _save_tokens(raw, path)
        saved = json.loads(path.read_text())
        assert saved["access_token"] == "acc123"
        assert saved["refresh_token"] == "ref456"
        assert saved["user_id"] == "ABC"
        assert saved["scope"] == "activity sleep"

    def test_expires_at_is_future(self, tmp_path):
        path = tmp_path / "tokens.json"
        raw = {
            "access_token": "a",
            "refresh_token": "r",
            "expires_in": 3600,
        }
        before = time.time()
        _save_tokens(raw, path)
        after = time.time()
        saved = json.loads(path.read_text())
        assert before + 3600 <= saved["expires_at"] <= after + 3600

    def test_missing_expires_in_defaults(self, tmp_path):
        path = tmp_path / "tokens.json"
        raw = {"access_token": "a", "refresh_token": "r"}
        _save_tokens(raw, path)
        saved = json.loads(path.read_text())
        # Дефолт 28800 секунд (~8 часов)
        assert saved["expires_at"] > time.time() + 28000
