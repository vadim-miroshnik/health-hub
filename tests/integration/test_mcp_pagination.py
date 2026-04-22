"""
P2.6: MCP heavy-result tools paginate via (limit, offset) with safe clamps.
"""

from pathlib import Path

import pytest

from src.db import Database


@pytest.fixture
def seeded_db(tmp_path: Path, migrations_dir: Path) -> Path:
    """A DB with 1000 hr_intraday rows for 2026-04-20 and a 1000-row O2Ring session."""
    db_path = tmp_path / "health.db"
    db = Database(db_path, migrations_dir)

    # hr_intraday: 1000 minutes
    entries = []
    for m in range(1000):
        hh, mm = divmod(m, 60)
        entries.append({"time": f"{hh:02d}:{mm:02d}:00", "value": 60 + (m % 30)})
    db.save_hr_intraday("2026-04-20", entries)

    # O2Ring session with 1000 data points
    sid = db.save_o2ring_session(
        date="2026-04-20",
        start_time="2026-04-20T22:00:00",
        end_time="2026-04-21T05:00:00",
        duration_minutes=420,
        avg_spo2=96.0, min_spo2=89.0, spo2_drops_count=3,
        avg_hr=62.0, min_hr=55, max_hr=80, o2_score=95.0,
    )
    data = [
        {
            "timestamp": f"2026-04-20T22:00:{i:02d}" if i < 60 else f"2026-04-20T22:{i // 60:02d}:{i % 60:02d}",
            "spo2": 95 + (i % 5),
            "heart_rate": 60 + (i % 20),
            "motion": i % 3,
        }
        for i in range(1000)
    ]
    db.save_o2ring_data(sid, data)
    db.close()
    return db_path


@pytest.fixture
def mcp_module(monkeypatch, seeded_db):
    import importlib
    monkeypatch.setenv("NO_DOTENV", "1")
    monkeypatch.setenv("DB_PATH", str(seeded_db))
    import mcp_server.server as mod
    importlib.reload(mod)
    return mod


class TestHrIntradayPagination:
    def test_default_returns_500(self, mcp_module):
        rows = mcp_module.get_hr_intraday("2026-04-20")
        assert len(rows) == 500

    def test_custom_limit(self, mcp_module):
        rows = mcp_module.get_hr_intraday("2026-04-20", limit=100)
        assert len(rows) == 100

    def test_offset_walks_results(self, mcp_module):
        page1 = mcp_module.get_hr_intraday("2026-04-20", limit=10, offset=0)
        page2 = mcp_module.get_hr_intraday("2026-04-20", limit=10, offset=10)
        assert len(page1) == 10 and len(page2) == 10
        assert page1[-1]["time"] < page2[0]["time"]

    def test_clamp_limit_to_max_2000(self, mcp_module):
        rows = mcp_module.get_hr_intraday("2026-04-20", limit=99999)
        assert len(rows) == 1000  # only 1000 seeded, but 2000 would also be fine

    def test_clamp_offset_to_zero(self, mcp_module):
        rows_neg = mcp_module.get_hr_intraday("2026-04-20", limit=5, offset=-50)
        rows_zero = mcp_module.get_hr_intraday("2026-04-20", limit=5, offset=0)
        assert rows_neg == rows_zero


class TestOximetryPagination:
    def test_default_returns_500(self, mcp_module):
        rows = mcp_module.get_oximetry_data("2026-04-20")
        assert len(rows) == 500

    def test_limit_and_offset(self, mcp_module):
        rows = mcp_module.get_oximetry_data("2026-04-20", limit=200, offset=300)
        assert len(rows) == 200

    def test_unknown_date_returns_empty(self, mcp_module):
        rows = mcp_module.get_oximetry_data("1999-01-01")
        assert rows == []

    def test_limit_above_max_clamped(self, mcp_module):
        rows = mcp_module.get_oximetry_data("2026-04-20", limit=5000)
        # Seeded 1000 points; clamping to 2000 still caps actual return at 1000
        assert len(rows) == 1000
