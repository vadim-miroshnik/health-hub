"""
P2.7: hhub backup — online SQLite snapshot + rotation to 30 most recent.
"""

import sqlite3
from pathlib import Path

import pytest

from src.cli.backup import cmd_backup, rotate_backups, run_backup
from src.db import Database


class TestRunBackup:
    def test_produces_valid_sqlite_file(self, tmp_path: Path, migrations_dir: Path):
        src = tmp_path / "health.db"
        db = Database(src, migrations_dir)
        db.save_activity("2026-04-20", steps=123)
        db.close()

        dest = tmp_path / "backup.db"
        run_backup(src, dest)

        assert dest.exists() and dest.stat().st_size > 0
        conn = sqlite3.connect(str(dest))
        try:
            row = conn.execute(
                "SELECT steps FROM daily_activity WHERE date='2026-04-20'"
            ).fetchone()
            assert row[0] == 123
        finally:
            conn.close()


class TestRotate:
    def _seed_fake(self, backup_dir: Path, dates: list[str]) -> None:
        for d in dates:
            (backup_dir / f"health-{d}.db").write_bytes(b"x")

    def test_keeps_most_recent(self, tmp_path: Path):
        # 31 day-stamped files — should keep 30 most recent
        dates = [f"202603{d:02d}" for d in range(1, 32)]  # 20260301..31
        self._seed_fake(tmp_path, dates)
        removed = rotate_backups(tmp_path, keep=30)

        remaining = sorted(p.name for p in tmp_path.glob("health-*.db"))
        assert len(remaining) == 30
        assert removed == 1
        # Oldest date 20260301 removed, newest 20260331 kept
        assert "health-20260301.db" not in remaining
        assert "health-20260331.db" in remaining

    def test_keeps_all_when_fewer_than_limit(self, tmp_path: Path):
        self._seed_fake(tmp_path, ["20260101", "20260102", "20260103"])
        removed = rotate_backups(tmp_path, keep=30)
        assert removed == 0
        assert len(list(tmp_path.glob("health-*.db"))) == 3

    def test_ignores_unrelated_files(self, tmp_path: Path):
        (tmp_path / "unrelated.db").write_bytes(b"x")
        (tmp_path / "health-20260101.db").write_bytes(b"x")
        rotate_backups(tmp_path, keep=30)
        assert (tmp_path / "unrelated.db").exists()


class TestCmdBackup:
    def test_cmd_writes_dated_backup(self, tmp_path: Path, monkeypatch, migrations_dir, capsys):
        src = tmp_path / "health.db"
        Database(src, migrations_dir).close()
        backups_dir = tmp_path / "backups"

        monkeypatch.setenv("DB_PATH", str(src))
        monkeypatch.setenv("BACKUP_DIR", str(backups_dir))

        cmd_backup()
        captured = capsys.readouterr()
        assert "backup:" in captured.out

        files = list(backups_dir.glob("health-*.db"))
        assert len(files) == 1
        assert files[0].name.startswith("health-")
        assert files[0].name.endswith(".db")
        assert len(files[0].name) == len("health-20260420.db")

    def test_cmd_errors_when_db_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "nope.db"))
        monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
        with pytest.raises(SystemExit) as exc:
            cmd_backup()
        assert exc.value.code == 1
