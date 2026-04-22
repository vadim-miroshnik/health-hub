"""
P1.2: `hhub serve-ingest` CLI smoke test.

Spawns the server as a subprocess and asserts that uvicorn actually binds the
requested port within 3 seconds. The server is shut down before the test
completes; we do not issue HTTP requests here (that's covered by
tests/integration/test_ingest_server.py via TestClient).
"""

import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listening(port: int, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(0.1)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except (ConnectionRefusedError, OSError):
                time.sleep(0.05)
    return False


def test_serve_ingest_refuses_without_token(tmp_path: Path):
    """Security fence: no token env var → exit 1 with a helpful error."""
    env = dict(os.environ, NO_DOTENV="1", HC_INGEST_AUTH_TOKEN="")
    port = _find_free_port()
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "serve-ingest", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "HC_INGEST_AUTH_TOKEN" in proc.stderr


def test_serve_ingest_binds_port(tmp_path: Path, migrations_dir: Path):
    """With a token, uvicorn binds the requested port within 3 seconds."""
    db_path = tmp_path / "health.db"
    # Pre-migrate so the ingest server has hc_records
    from src.db import Database
    Database(db_path, migrations_dir).close()

    port = _find_free_port()
    env = dict(
        os.environ,
        NO_DOTENV="1",
        HC_INGEST_AUTH_TOKEN="test-secret",
        DB_PATH=str(db_path),
        RAW_DIR=str(tmp_path / "raw"),
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.main", "serve-ingest",
         "--port", str(port), "--host", "127.0.0.1"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert _wait_listening(port, timeout=3.0), (
            "serve-ingest did not bind within 3s"
        )
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_deploy_doc_exists_with_systemd_template():
    """docs/deploy.md documents systemd unit + curl smoke path."""
    doc = REPO_ROOT / "docs" / "deploy.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert "health-hub-ingest.service" in text
    assert "ExecStart=" in text
    assert "Restart=on-failure" in text
    assert "HC_INGEST_AUTH_TOKEN" in text
    assert "hhub serve-ingest" in text
    assert "/ingest/health-connect" in text  # curl example present
