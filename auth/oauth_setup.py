"""
Первичная OAuth2 авторизация Fitbit (Personal app, PKCE).

Запуск: python auth/oauth_setup.py
  или:  make auth

Что делает:
1. Генерирует PKCE code_verifier / code_challenge
2. Открывает браузер на странице авторизации Fitbit
3. Принимает callback на http://127.0.0.1:8080/callback
4. Обменивает code → tokens
5. Сохраняет tokens.json

Требования в .env:
  FITBIT_CLIENT_ID
  FITBIT_CLIENT_SECRET
  TOKENS_PATH  (опционально, по умолчанию tokens.json)
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

AUTHORIZE_URL = "https://www.fitbit.com/oauth2/authorize"
TOKEN_URL = "https://api.fitbit.com/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:8080/callback"
SCOPE = "activity heartrate sleep weight nutrition profile"
PORT = 8080


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _generate_code_verifier() -> str:
    """Случайная строка 96 байт → base64url (128 символов, разрешён диапазон)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(96)).rstrip(b"=").decode()


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# Local callback server
# ---------------------------------------------------------------------------

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Принимает один GET /callback?code=...&state=... и останавливает сервер."""

    result: dict = {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path != "/callback":
            self._respond(404, "Not found")
            return

        if "error" in params:
            _CallbackHandler.result = {"error": params["error"][0]}
            self._respond(400, f"Authorization error: {params['error'][0]}")
        elif "code" in params:
            _CallbackHandler.result = {
                "code": params["code"][0],
                "state": params.get("state", [None])[0],
            }
            self._respond(200, "Authorization successful. You can close this tab.")
        else:
            _CallbackHandler.result = {"error": "missing_code"}
            self._respond(400, "Missing code parameter")

        # Останавливаем сервер после первого запроса
        self.server._BaseServer__shutdown_request = True  # noqa: SLF001

    def _respond(self, code: int, message: str) -> None:
        body = message.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # silence default access log
        pass


def _wait_for_callback(expected_state: str) -> str:
    """Запускает HTTP-сервер и ждёт OAuth callback. Возвращает code."""
    server = http.server.HTTPServer(("127.0.0.1", PORT), _CallbackHandler)
    print(f"  Ожидаю callback на http://127.0.0.1:{PORT}/callback ...")
    server.handle_request()  # обрабатываем ровно один запрос

    result = _CallbackHandler.result
    if "error" in result:
        raise RuntimeError(f"OAuth error: {result['error']}")
    if result.get("state") != expected_state:
        raise RuntimeError("State mismatch — возможна CSRF-атака")
    return result["code"]


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

def _exchange_code(code: str, verifier: str, client_id: str, client_secret: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _save_tokens(raw: dict, path: Path) -> None:
    tokens = {
        "access_token": raw["access_token"],
        "refresh_token": raw["refresh_token"],
        "expires_at": time.time() + int(raw.get("expires_in", 28800)),
        "user_id": raw.get("user_id", ""),
        "scope": raw.get("scope", SCOPE),
    }
    path.write_text(json.dumps(tokens, indent=2))
    print(f"  Токены сохранены → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    client_id = os.environ.get("FITBIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("FITBIT_CLIENT_SECRET", "").strip()
    tokens_path = Path(os.environ.get("TOKENS_PATH", "tokens.json"))

    if not client_id or not client_secret:
        raise SystemExit(
            "Не заданы FITBIT_CLIENT_ID / FITBIT_CLIENT_SECRET в .env"
        )

    verifier = _generate_code_verifier()
    challenge = _code_challenge(verifier)
    state = secrets.token_urlsafe(16)

    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    auth_url = f"{AUTHORIZE_URL}?{params}"

    print("Открываю браузер для авторизации Fitbit...")
    print(f"  URL: {auth_url}\n")
    webbrowser.open(auth_url)

    code = _wait_for_callback(expected_state=state)
    print("  Получен authorization code, обмениваю на токены...")

    raw = _exchange_code(code, verifier, client_id, client_secret)
    _save_tokens(raw, tokens_path)
    print("Авторизация завершена.")


if __name__ == "__main__":
    main()
