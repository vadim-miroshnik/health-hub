"""Тесты для src/migrations.py."""

import sqlite3
from pathlib import Path

import pytest

from src.migrations import _current_version, run_migrations

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# Применение всех миграций на пустой БД
# ---------------------------------------------------------------------------

class TestRunMigrations:
    def test_applies_all_migrations(self, conn):
        applied = run_migrations(conn, MIGRATIONS_DIR)
        assert applied == 4

    def test_schema_version_updated(self, conn):
        run_migrations(conn, MIGRATIONS_DIR)
        version = _current_version(conn)
        assert version == 4

    def test_all_fitbit_tables_created(self, conn):
        run_migrations(conn, MIGRATIONS_DIR)
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "schema_version", "raw_files",
            "daily_nutrition", "daily_activity", "sleep_sessions",
            "sleep_stages", "daily_weight", "daily_hrv",
            "food_log", "sync_log",
        }
        assert expected <= tables

    def test_cpap_tables_created(self, conn):
        run_migrations(conn, MIGRATIONS_DIR)
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"cpap_sessions", "cpap_events"} <= tables

    def test_o2ring_tables_created(self, conn):
        run_migrations(conn, MIGRATIONS_DIR)
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"o2ring_sessions", "o2ring_data"} <= tables


# ---------------------------------------------------------------------------
# Идемпотентность
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_second_run_applies_nothing(self, conn):
        run_migrations(conn, MIGRATIONS_DIR)
        applied = run_migrations(conn, MIGRATIONS_DIR)
        assert applied == 0

    def test_schema_version_unchanged_on_repeat(self, conn):
        run_migrations(conn, MIGRATIONS_DIR)
        run_migrations(conn, MIGRATIONS_DIR)
        assert _current_version(conn) == 4

    def test_tables_not_duplicated(self, conn):
        run_migrations(conn, MIGRATIONS_DIR)
        run_migrations(conn, MIGRATIONS_DIR)
        count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='daily_nutrition'"
        ).fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Частичное применение (N → N+2 через N+1)
# ---------------------------------------------------------------------------

class TestPartialMigrations:
    def test_applies_only_pending(self, tmp_path, conn):
        """Если уже применена миграция 1, применяются только 2 и 3."""
        # Создаём каталог с одной миграцией и применяем
        m1 = MIGRATIONS_DIR / "001_initial.sql"
        custom_dir = tmp_path / "migrations"
        custom_dir.mkdir()
        (custom_dir / "001_initial.sql").write_text(m1.read_text())
        run_migrations(conn, custom_dir)

        assert _current_version(conn) == 1

        # Добавляем оставшиеся миграции
        for name in ["002_add_cpap.sql", "003_add_o2ring.sql"]:
            src = MIGRATIONS_DIR / name
            (custom_dir / name).write_text(src.read_text())

        applied = run_migrations(conn, custom_dir)
        assert applied == 2
        assert _current_version(conn) == 3

    def test_schema_version_increments_per_migration(self, tmp_path, conn):
        custom_dir = tmp_path / "migrations"
        custom_dir.mkdir()

        versions = []
        for name in ["001_initial.sql", "002_add_cpap.sql", "003_add_o2ring.sql"]:
            (custom_dir / name).write_text((MIGRATIONS_DIR / name).read_text())
            run_migrations(conn, custom_dir)
            versions.append(_current_version(conn))

        assert versions == [1, 2, 3]


# ---------------------------------------------------------------------------
# Пустая директория / нет миграций
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_dir_applies_nothing(self, tmp_path, conn):
        empty = tmp_path / "empty"
        empty.mkdir()
        applied = run_migrations(conn, empty)
        assert applied == 0

    def test_version_zero_on_fresh_db(self, conn):
        assert _current_version(conn) == 0

    def test_ignores_non_sql_files(self, tmp_path, conn):
        (tmp_path / "README.md").write_text("not a migration")
        (tmp_path / "001_initial.sql").write_text(
            (MIGRATIONS_DIR / "001_initial.sql").read_text()
        )
        applied = run_migrations(conn, tmp_path)
        assert applied == 1
