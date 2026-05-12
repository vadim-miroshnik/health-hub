"""
MCP Health Connect tools — drive the in-process functions registered as
@mcp.tool() and assert they return correctly-shaped data against a DB
populated through the real ingest path (no synthetic fixture inserts —
end-to-end from POST to read).
"""

import importlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def hc_db(tmp_path: Path, monkeypatch, migrations_dir):
    db_path = tmp_path / "health.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    from src.db import Database
    Database(db_path, migrations_dir).close()

    monkeypatch.setenv("NO_DOTENV", "1")
    monkeypatch.setenv("HC_INGEST_AUTH_TOKEN", "test-secret")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("RAW_DIR", str(raw_dir))

    import src.ingest_server as srv
    importlib.reload(srv)
    import mcp_server.server as mcp_srv
    importlib.reload(mcp_srv)
    return srv, mcp_srv, db_path


def _push(client: TestClient, records: list[dict], batch_id: str = "b1") -> None:
    r = client.post(
        "/ingest/health-connect",
        json={"batch_id": batch_id, "synced_at": "2026-05-11T08:00:00Z", "records": records},
        headers={"X-Auth-Token": "test-secret"},
    )
    assert r.status_code == 200, r.text


class TestSleep:
    def test_get_hc_sleep_aggregates_stages(self, hc_db):
        srv, mcp_srv, _ = hc_db
        client = TestClient(srv.app)
        _push(client, [{
            "uid": "sleep-1", "type": "SleepSession",
            "start_time": "2026-05-10T22:00:00Z",
            "end_time":   "2026-05-11T05:00:00Z",
            "source_app": "com.fitbit.FitbitMobile",
            "stages": [
                {"stage": "deep",  "start": "2026-05-10T22:00:00Z",
                 "end": "2026-05-10T23:00:00Z"},
                {"stage": "light", "start": "2026-05-10T23:00:00Z",
                 "end": "2026-05-11T02:00:00Z"},
                {"stage": "rem",   "start": "2026-05-11T02:00:00Z",
                 "end": "2026-05-11T03:00:00Z"},
                {"stage": "awake", "start": "2026-05-11T03:00:00Z",
                 "end": "2026-05-11T05:00:00Z"},
            ],
        }])

        result = mcp_srv.get_hc_sleep("2026-05-11")
        assert len(result) == 1
        s = result[0]
        assert s["total_minutes"] == 420.0  # 22:00 → 05:00 = 7h
        assert s["stages_minutes"] == {"deep": 60.0, "light": 180.0, "rem": 60.0, "awake": 120.0}
        assert len(s["stages_raw"]) == 4
        assert s["source_app"] == "com.fitbit.FitbitMobile"


class TestHRV:
    def test_get_hc_hrv_via_view(self, hc_db):
        srv, mcp_srv, _ = hc_db
        client = TestClient(srv.app)
        _push(client, [
            {"uid": f"hrv-{i}", "type": "HeartRateVariabilityRmssd",
             "start_time": f"2026-05-11T0{i}:00:00Z",
             "end_time":   f"2026-05-11T0{i}:00:00Z",
             "value": v, "unit": "ms"}
            for i, v in enumerate([40.0, 50.0, 60.0])
        ])

        hrv = mcp_srv.get_hc_hrv("2026-05-11")
        assert hrv is not None
        assert hrv["measurements"] == 3
        assert hrv["min_rmssd"] == 40.0
        assert hrv["max_rmssd"] == 60.0
        assert abs(hrv["avg_rmssd"] - 50.0) < 0.01


class TestActivity:
    def test_get_hc_steps_sums_records(self, hc_db):
        srv, mcp_srv, _ = hc_db
        client = TestClient(srv.app)
        _push(client, [
            {"uid": f"steps-{i}", "type": "Steps",
             "start_time": f"2026-05-11T1{i}:00:00Z",
             "end_time":   f"2026-05-11T1{i}:30:00Z",
             "value": v, "unit": "count"}
            for i, v in enumerate([1000, 2500, 1800])
        ])
        assert mcp_srv.get_hc_steps("2026-05-11") == 5300

    def test_get_hc_steps_returns_none_when_no_data(self, hc_db):
        _, mcp_srv, _ = hc_db
        assert mcp_srv.get_hc_steps("2026-05-11") is None


class TestRecordTypes:
    def test_returns_per_type_counts(self, hc_db):
        srv, mcp_srv, _ = hc_db
        client = TestClient(srv.app)
        _push(client, [
            {"uid": "hr-1", "type": "HeartRate", "start_time": "2026-05-11T10:00:00Z",
             "end_time": "2026-05-11T10:00:00Z", "value": 70},
            {"uid": "hr-2", "type": "HeartRate", "start_time": "2026-05-11T10:01:00Z",
             "end_time": "2026-05-11T10:01:00Z", "value": 72},
            {"uid": "w-1",  "type": "Weight",    "start_time": "2026-05-11T08:00:00Z",
             "end_time": "2026-05-11T08:00:00Z", "value": 80.5, "unit": "kg"},
        ])
        types = mcp_srv.get_hc_record_types("2026-05-11")
        counts = {r["type"]: r["count"] for r in types}
        assert counts == {"HeartRate": 2, "Weight": 1}


class TestBodyRange:
    def test_get_hc_weight_range_returns_samples_in_order(self, hc_db):
        srv, mcp_srv, _ = hc_db
        client = TestClient(srv.app)
        _push(client, [
            {"uid": "w-1", "type": "Weight",
             "start_time": "2026-05-09T08:00:00Z",
             "end_time":   "2026-05-09T08:00:00Z",
             "value": 80.5, "unit": "kg", "source_app": "com.scale.app"},
            {"uid": "w-2", "type": "Weight",
             "start_time": "2026-05-10T08:00:00Z",
             "end_time":   "2026-05-10T08:00:00Z",
             "value": 80.2, "unit": "kg", "source_app": "com.scale.app"},
            {"uid": "w-3", "type": "Weight",
             "start_time": "2026-05-11T08:00:00Z",
             "end_time":   "2026-05-11T08:00:00Z",
             "value": 79.9, "unit": "kg", "source_app": "com.scale.app"},
        ])

        rng = mcp_srv.get_hc_weight_range("2026-05-09", "2026-05-11")
        assert [r["value"] for r in rng] == [80.5, 80.2, 79.9]
        assert all(r["unit"] == "kg" for r in rng)

        # Window filtering: only middle day.
        mid = mcp_srv.get_hc_weight_range("2026-05-10", "2026-05-10")
        assert len(mid) == 1
        assert mid[0]["value"] == 80.2

    def test_get_hc_body_composition_range_mixes_types(self, hc_db):
        srv, mcp_srv, _ = hc_db
        client = TestClient(srv.app)
        _push(client, [
            {"uid": "bf-1", "type": "BodyFat",
             "start_time": "2026-05-10T08:00:00Z",
             "end_time":   "2026-05-10T08:00:00Z",
             "value": 22.1, "unit": "%"},
            {"uid": "bw-1", "type": "BodyWaterMass",
             "start_time": "2026-05-10T08:01:00Z",
             "end_time":   "2026-05-10T08:01:00Z",
             "value": 45.0, "unit": "kg"},
            {"uid": "bf-2", "type": "BodyFat",
             "start_time": "2026-05-11T08:00:00Z",
             "end_time":   "2026-05-11T08:00:00Z",
             "value": 21.8, "unit": "%"},
        ])

        rng = mcp_srv.get_hc_body_composition_range("2026-05-09", "2026-05-12")
        assert [r["type"]  for r in rng] == ["BodyFat", "BodyWaterMass", "BodyFat"]
        assert [r["value"] for r in rng] == [22.1, 45.0, 21.8]


class TestRawRecords:
    def test_get_hc_records_filter_by_type(self, hc_db):
        srv, mcp_srv, _ = hc_db
        client = TestClient(srv.app)
        _push(client, [
            {"uid": "bp-1", "type": "BloodPressure",
             "start_time": "2026-05-11T08:00:00Z",
             "end_time": "2026-05-11T08:00:00Z",
             "systolic": 120, "diastolic": 80},
        ])
        result = mcp_srv.get_hc_records("2026-05-11", type="BloodPressure")
        assert len(result) == 1
        assert result[0]["type"] == "BloodPressure"
        # extra="allow" must have preserved the nested systolic/diastolic
        assert result[0]["data"]["systolic"] == 120
        assert result[0]["data"]["diastolic"] == 80
