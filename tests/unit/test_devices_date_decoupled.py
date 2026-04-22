"""
P0.5: `devices` snapshot is date-independent. Collector must not key the
devices raw file by date, and Database.save_devices must not accept `date`.
"""

import inspect

import pytest

from src.db import Database


def test_save_devices_signature_has_no_date() -> None:
    params = inspect.signature(Database.save_devices).parameters
    assert "date" not in params, (
        f"Database.save_devices should not take a 'date' param; got {list(params)}"
    )


def test_collector_does_not_raw_save_devices_per_date(tmp_path, monkeypatch):
    """
    Scan src/collector.py source: the devices endpoint handler must not call
    self.store.save_raw(..., "devices", ...). Devices snapshot lives in the
    `devices` table only; raw-archiving is redundant.
    """
    import pathlib
    src = pathlib.Path(__file__).parent.parent.parent / "src" / "collector.py"
    text = src.read_text(encoding="utf-8")
    # The only mention of "devices" as a kind should be in comments / table calls,
    # not in self.store.save_raw(...).
    assert 'save_raw("fitbit", date, "devices"' not in text
    assert "save_raw('fitbit', date, 'devices'" not in text


def test_save_devices_accepts_list(db) -> None:
    """Smoke test: signature change didn't break the positional call."""
    db.save_devices([
        {
            "id": "t1",
            "deviceVersion": "Versa 4",
            "battery": "High",
            "batteryLevel": 80,
            "lastSyncTime": "2026-04-15T20:00:00",
            "type": "TRACKER",
        }
    ])
    rows = db.get_devices()
    assert len(rows) == 1
    assert rows[0]["id"] == "t1"
