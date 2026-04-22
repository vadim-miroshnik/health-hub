"""
Integration tests for MCP server read-only connection pattern (P0.2).

MCP tools must open a fresh read-only SQLite connection per call, so that
concurrent dispatch from FastMCP worker threads does not share sqlite3
objects across threads, and writes from MCP paths fail loudly.
"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from src.db import Database


@pytest.fixture
def populated_db_path(tmp_path: Path, migrations_dir: Path) -> Path:
    """Create a real on-disk DB with one row in daily_activity."""
    db_path = tmp_path / "health.db"
    db = Database(db_path, migrations_dir)
    db.save_activity("2026-04-20", steps=5000)
    db.close()
    return db_path


def test_readonly_connection_opens(populated_db_path: Path, migrations_dir: Path) -> None:
    """readonly=True opens a ?mode=ro URI connection."""
    db = Database(populated_db_path, migrations_dir, readonly=True)
    row = db.get_activity("2026-04-20")
    assert row is not None
    assert row["steps"] == 5000
    db.close()


def test_readonly_write_raises(populated_db_path: Path, migrations_dir: Path) -> None:
    """Attempting a write via a readonly Database raises OperationalError."""
    db = Database(populated_db_path, migrations_dir, readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.save_activity("2026-04-21", steps=1)
    finally:
        db.close()


def test_mcp_db_opens_readonly(monkeypatch, populated_db_path: Path) -> None:
    """mcp_server.server._db() returns a readonly Database for the configured path."""
    monkeypatch.setenv("NO_DOTENV", "1")
    monkeypatch.setenv("DB_PATH", str(populated_db_path))
    # Reload to pick up env
    import importlib

    import mcp_server.server as server_mod
    importlib.reload(server_mod)

    db = server_mod._db()
    try:
        assert db.get_activity("2026-04-20") is not None
        with pytest.raises(sqlite3.OperationalError):
            db.save_activity("2026-04-21", steps=1)
    finally:
        db.close()


def test_concurrent_mcp_calls_no_thread_errors(monkeypatch, populated_db_path: Path) -> None:
    """
    50 concurrent threads call an MCP tool function; none raise ProgrammingError
    (which sqlite3 raises when a connection is used across threads).
    """
    monkeypatch.setenv("NO_DOTENV", "1")
    monkeypatch.setenv("DB_PATH", str(populated_db_path))

    import importlib
    import mcp_server.server as server_mod
    importlib.reload(server_mod)

    def call_tool() -> dict | None:
        # Use get_activity which is a simple read
        db = server_mod._db()
        try:
            return db.get_activity("2026-04-20")
        finally:
            db.close()

    results: list = []
    errors: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(call_tool) for _ in range(50)]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    assert not errors, f"Thread errors: {errors[:3]}"
    assert len(results) == 50
    assert all(r is not None and r["steps"] == 5000 for r in results)


def test_no_shared_singleton_attribute() -> None:
    """Old `_SharedDB` / `_shared_db` singleton must be removed."""
    import mcp_server.server as server_mod
    assert not hasattr(server_mod, "_SharedDB"), "_SharedDB class must be removed"
    assert not hasattr(server_mod, "_shared_db"), "_shared_db global must be removed"
