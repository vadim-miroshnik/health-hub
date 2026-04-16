import struct
import unittest.mock as mock
from pathlib import Path

import pytest

from src.o2ring_collector import O2RingCollector, _count_desaturations, _parse_time


# ---------------------------------------------------------------------------
# Helper: build synthetic CSV content
# ---------------------------------------------------------------------------

def _make_csv(records: list[tuple]) -> str:
    """records: [(time_str, spo2, hr, motion), ...]"""
    lines = ["Time,SpO2(%),Pulse Rate(bpm),Motion"]
    for t, s, h, m in records:
        lines.append(f"{t},{s},{h},{m}")
    return "\n".join(lines)


def _make_binary(records: list[tuple]) -> bytes:
    """records: [(spo2, hr, motion), ...], prepend 40-byte header"""
    header = b'\x00' * 40
    body = b"".join(struct.pack("<BBBxx", s, h, m) for s, h, m in records)
    return header + body


# ---------------------------------------------------------------------------
# _count_desaturations
# ---------------------------------------------------------------------------

class TestCountDesaturations:
    def test_no_drops(self):
        assert _count_desaturations([97] * 100) == 0

    def test_single_drop(self):
        # Stable at 97, then drop to 93 for 5 samples (20 sec) — 1 event
        vals = [97] * 20 + [93] * 5 + [97] * 20
        assert _count_desaturations(vals) >= 1

    def test_short_drop_ignored(self):
        # Drop for only 1 sample (4 sec < 10 sec minimum)
        vals = [97] * 20 + [93] * 1 + [97] * 20
        assert _count_desaturations(vals) == 0

    def test_empty(self):
        assert _count_desaturations([]) == 0

    def test_two_separate_events(self):
        vals = [97] * 10 + [93] * 5 + [97] * 20 + [93] * 5 + [97] * 10
        assert _count_desaturations(vals) >= 2


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

class TestO2RingCsvImport:
    def test_basic_import(self, db, tmp_path):
        csv_content = _make_csv([
            ("00:00:00", 97, 68, 0),
            ("00:00:04", 96, 67, 0),
            ("00:00:08", 97, 69, 0),
        ])
        f = tmp_path / "session.csv"
        f.write_text(csv_content)

        collector = O2RingCollector(db, mock.MagicMock())
        result = collector.import_csv("2026-04-15", f)
        assert result.status == "ok"
        assert result.n_records == 3

    def test_session_saved_to_db(self, db, tmp_path):
        csv_content = _make_csv([
            ("00:00:00", 97, 68, 0),
            ("00:00:04", 95, 67, 0),
            ("00:00:08", 96, 66, 1),
        ])
        f = tmp_path / "session.csv"
        f.write_text(csv_content)

        collector = O2RingCollector(db, mock.MagicMock())
        collector.import_csv("2026-04-15", f)

        session = db.get_o2ring_session("2026-04-15")
        assert session is not None
        assert session["avg_spo2"] is not None
        assert session["min_spo2"] == 95

    def test_skips_header_lines(self, db, tmp_path):
        # Lines that can't be parsed as HH:MM:SS should be skipped
        content = "O2Ring Session Export\nTime,SpO2(%),Pulse Rate(bpm),Motion\n00:00:00,97,68,0\n"
        f = tmp_path / "session.csv"
        f.write_text(content)

        collector = O2RingCollector(db, mock.MagicMock())
        result = collector.import_csv("2026-04-15", f)
        assert result.status == "ok"
        assert result.n_records == 1

    def test_nonexistent_file_returns_error(self, db, tmp_path):
        collector = O2RingCollector(db, mock.MagicMock())
        result = collector.import_csv("2026-04-15", tmp_path / "missing.csv")
        assert result.status == "error"


# ---------------------------------------------------------------------------
# Binary parsing
# ---------------------------------------------------------------------------

class TestO2RingBinaryImport:
    def test_basic_binary_import(self, db, tmp_path):
        data = _make_binary([(97, 68, 0), (96, 67, 0), (97, 69, 0)])
        f = tmp_path / "session.bin"
        f.write_bytes(data)

        collector = O2RingCollector(db, mock.MagicMock())
        result = collector.import_binary("2026-04-15", f)
        assert result.status == "ok"
        assert result.n_records == 3

    def test_zero_records_filtered(self, db, tmp_path):
        # Records with spo2=0 or hr=0 are filtered (off-finger)
        data = _make_binary([(0, 0, 0), (97, 68, 0), (0, 0, 0)])
        f = tmp_path / "session.bin"
        f.write_bytes(data)

        collector = O2RingCollector(db, mock.MagicMock())
        result = collector.import_binary("2026-04-15", f)
        assert result.n_records == 1

    def test_too_small_file_returns_error(self, db, tmp_path):
        f = tmp_path / "tiny.bin"
        f.write_bytes(b"\x00" * 10)

        collector = O2RingCollector(db, mock.MagicMock())
        result = collector.import_binary("2026-04-15", f)
        assert result.status == "error"
