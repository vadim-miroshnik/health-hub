"""Общие pytest-фикстуры для всех тестов."""

import sqlite3
from pathlib import Path

import pytest

from src.db import Database
from src.migrations import run_migrations
from src.raw_store import RawStore

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


@pytest.fixture
def migrations_dir() -> Path:
    return MIGRATIONS_DIR


@pytest.fixture
def raw_conn(migrations_dir) -> sqlite3.Connection:
    """In-memory соединение SQLite с применёнными миграциями."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    run_migrations(conn, migrations_dir)
    return conn


@pytest.fixture
def db(tmp_path, migrations_dir) -> Database:
    """Database в tmp_path."""
    return Database(tmp_path / "test.db", migrations_dir)


@pytest.fixture
def store(raw_conn, tmp_path) -> RawStore:
    """RawStore с in-memory SQLite и tmp_path/raw как базовой директорией."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    return RawStore(raw_conn, raw_dir)
