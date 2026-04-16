"""
Система миграций схемы SQLite.

Применяет все файлы migrations/NNN_*.sql с версией выше текущей.
Каждый файл — одна транзакция, идемпотентная (IF NOT EXISTS).

Использование:
    from src.migrations import run_migrations
    run_migrations(conn)
"""

import re
import sqlite3
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
_VERSION_PATTERN = re.compile(r"^(\d+)_.*\.sql$")


def _current_version(conn: sqlite3.Connection) -> int:
    """Возвращает текущую версию схемы (0 если таблица ещё не создана)."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] or 0
    except sqlite3.OperationalError:
        return 0


def run_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path = _MIGRATIONS_DIR,
) -> int:
    """
    Применяет все ожидающие миграции из migrations_dir.

    Возвращает количество применённых миграций.
    Миграции применяются строго по порядку номеров.
    """
    current = _current_version(conn)

    pending: list[tuple[int, Path]] = []
    for path in migrations_dir.glob("*.sql"):
        match = _VERSION_PATTERN.match(path.name)
        if match:
            version = int(match.group(1))
            if version > current:
                pending.append((version, path))

    applied = 0
    for version, path in sorted(pending):
        sql = path.read_text(encoding="utf-8")
        # Strip single-line comments and split into individual statements.
        # executescript() is intentionally avoided: it issues an implicit COMMIT
        # before running, which breaks transactional safety and can leave the
        # schema partially migrated on failure.
        statements = [
            s.strip()
            for s in re.sub(r"--[^\n]*", "", sql).split(";")
            if s.strip()
        ]
        # Each migration file already contains its own BEGIN/COMMIT.
        # Switch to manual (autocommit) mode so Python's implicit transaction
        # management doesn't interfere with the file's own transaction block.
        # Also flush any implicit transaction opened by PRAGMA calls first.
        if conn.in_transaction:
            conn.commit()
        old_isolation = conn.isolation_level
        conn.isolation_level = None
        try:
            for stmt in statements:
                conn.execute(stmt)
        finally:
            conn.isolation_level = old_isolation

        applied += 1

    return applied
