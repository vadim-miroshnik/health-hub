"""Тесты для src/raw_store.py."""

import json
import time

import pytest


class TestSaveRaw:
    def test_creates_file_on_disk(self, store, tmp_path):
        store.save_raw("fitbit", "2026-04-15", "sleep", '{"sleep": []}')
        path = tmp_path / "raw" / "fitbit" / "2026-04-15" / "sleep.json"
        assert path.exists()

    def test_file_content_str(self, store, tmp_path):
        content = '{"key": "value"}'
        store.save_raw("fitbit", "2026-04-15", "nutrition", content)
        path = tmp_path / "raw" / "fitbit" / "2026-04-15" / "nutrition.json"
        assert path.read_text() == content

    def test_file_content_bytes(self, store, tmp_path):
        data = b"\x00\x01\x02\x03"
        store.save_raw("o2ring", "2026-04-15", "session_bin", data)
        path = tmp_path / "raw" / "o2ring" / "2026-04-15" / "session_bin.bin"
        assert path.read_bytes() == data

    def test_registers_in_raw_files(self, store, raw_conn):
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        row = raw_conn.execute(
            "SELECT * FROM raw_files WHERE source='fitbit' AND date='2026-04-15' AND kind='sleep'"
        ).fetchone()
        assert row is not None

    def test_stores_correct_size(self, store, raw_conn):
        content = "x" * 100
        store.save_raw("fitbit", "2026-04-15", "activity", content)
        row = raw_conn.execute(
            "SELECT size_bytes FROM raw_files WHERE kind='activity'"
        ).fetchone()
        assert row["size_bytes"] == 100

    def test_stores_relative_filepath(self, store, raw_conn):
        store.save_raw("fitbit", "2026-04-15", "hrv", "{}")
        row = raw_conn.execute(
            "SELECT filepath FROM raw_files WHERE kind='hrv'"
        ).fetchone()
        # Путь должен быть относительным (не начинаться с /)
        assert not row["filepath"].startswith("/")
        assert "fitbit/2026-04-15/hrv.json" in row["filepath"]

    def test_creates_parent_dirs(self, store, tmp_path):
        # Директория не существовала до вызова
        store.save_raw("cpap", "2026-01-01", "session", b"\xff")
        assert (tmp_path / "raw" / "cpap" / "2026-01-01").is_dir()

    def test_returns_absolute_path(self, store, tmp_path):
        path = store.save_raw("fitbit", "2026-04-15", "weight", "{}")
        assert path.is_absolute()
        assert path.exists()

    def test_edf_extension(self, store, tmp_path):
        store.save_raw("cpap", "2026-04-15", "pressure_edf", b"EDF")
        path = tmp_path / "raw" / "cpap" / "2026-04-15" / "pressure_edf.edf"
        assert path.exists()

    def test_csv_extension(self, store, tmp_path):
        store.save_raw("o2ring", "2026-04-15", "export_csv", b"ts,spo2")
        path = tmp_path / "raw" / "o2ring" / "2026-04-15" / "export_csv.csv"
        assert path.exists()


class TestOverwrite:
    def test_overwrite_updates_content(self, store, tmp_path):
        store.save_raw("fitbit", "2026-04-15", "sleep", '{"v": 1}')
        store.save_raw("fitbit", "2026-04-15", "sleep", '{"v": 2}')
        path = tmp_path / "raw" / "fitbit" / "2026-04-15" / "sleep.json"
        assert json.loads(path.read_text())["v"] == 2

    def test_overwrite_updates_fetched_at(self, store, raw_conn):
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        first_ts = raw_conn.execute(
            "SELECT fetched_at FROM raw_files WHERE kind='sleep'"
        ).fetchone()["fetched_at"]

        time.sleep(0.01)
        store.save_raw("fitbit", "2026-04-15", "sleep", "{updated}")
        second_ts = raw_conn.execute(
            "SELECT fetched_at FROM raw_files WHERE kind='sleep'"
        ).fetchone()["fetched_at"]

        assert second_ts > first_ts

    def test_overwrite_updates_size(self, store, raw_conn):
        store.save_raw("fitbit", "2026-04-15", "nutrition", "x" * 10)
        store.save_raw("fitbit", "2026-04-15", "nutrition", "x" * 50)
        row = raw_conn.execute(
            "SELECT size_bytes FROM raw_files WHERE kind='nutrition'"
        ).fetchone()
        assert row["size_bytes"] == 50

    def test_no_duplicate_rows_on_overwrite(self, store, raw_conn):
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        count = raw_conn.execute(
            "SELECT COUNT(*) FROM raw_files WHERE source='fitbit' AND date='2026-04-15' AND kind='sleep'"
        ).fetchone()[0]
        assert count == 1


class TestGetRaw:
    def test_returns_correct_path(self, store, tmp_path):
        store.save_raw("fitbit", "2026-04-15", "sleep", '{"ok": true}')
        path = store.get_raw("fitbit", "2026-04-15", "sleep")
        assert path.exists()
        assert json.loads(path.read_text()) == {"ok": True}

    def test_raises_for_unregistered_file(self, store):
        with pytest.raises(FileNotFoundError, match="not found"):
            store.get_raw("fitbit", "2020-01-01", "missing")

    def test_raises_if_file_deleted_from_disk(self, store, tmp_path):
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        # Удаляем файл с диска, запись в БД остаётся
        (tmp_path / "raw" / "fitbit" / "2026-04-15" / "sleep.json").unlink()
        with pytest.raises(FileNotFoundError, match="missing on disk"):
            store.get_raw("fitbit", "2026-04-15", "sleep")


class TestListRaw:
    def test_returns_all_for_source(self, store):
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        store.save_raw("fitbit", "2026-04-14", "nutrition", "{}")
        store.save_raw("cpap", "2026-04-15", "session", b"")

        rows = store.list_raw("fitbit")
        assert len(rows) == 2
        assert all(r["source"] == "fitbit" for r in rows)

    def test_filter_start_date(self, store):
        store.save_raw("fitbit", "2026-04-10", "sleep", "{}")
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        store.save_raw("fitbit", "2026-04-20", "sleep", "{}")

        rows = store.list_raw("fitbit", start_date="2026-04-15")
        dates = [r["date"] for r in rows]
        assert "2026-04-10" not in dates
        assert "2026-04-15" in dates
        assert "2026-04-20" in dates

    def test_filter_end_date(self, store):
        store.save_raw("fitbit", "2026-04-10", "sleep", "{}")
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        store.save_raw("fitbit", "2026-04-20", "sleep", "{}")

        rows = store.list_raw("fitbit", end_date="2026-04-15")
        dates = [r["date"] for r in rows]
        assert "2026-04-10" in dates
        assert "2026-04-15" in dates
        assert "2026-04-20" not in dates

    def test_filter_date_range(self, store):
        for d in ["2026-04-10", "2026-04-15", "2026-04-20"]:
            store.save_raw("fitbit", d, "sleep", "{}")
        rows = store.list_raw("fitbit", start_date="2026-04-12", end_date="2026-04-18")
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-04-15"

    def test_empty_for_unknown_source(self, store):
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        assert store.list_raw("o2ring") == []

    def test_result_has_all_fields(self, store):
        store.save_raw("fitbit", "2026-04-15", "sleep", "{}")
        row = store.list_raw("fitbit")[0]
        assert set(row.keys()) == {"source", "date", "kind", "filepath", "fetched_at", "size_bytes"}
