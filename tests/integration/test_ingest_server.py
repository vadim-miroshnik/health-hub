"""
P1.1: HTTP ingest server — happy path, dedup, auth.

Uses FastAPI TestClient which drives the app in-process synchronously.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def configured_env(tmp_path: Path, monkeypatch, migrations_dir):
    """Wire env vars so the ingest server points at a tmp DB and raw dir."""
    db_path = tmp_path / "health.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    # Migrate the DB once so hc_records table exists
    from src.db import Database
    Database(db_path, migrations_dir).close()

    monkeypatch.setenv("NO_DOTENV", "1")
    monkeypatch.setenv("HC_INGEST_AUTH_TOKEN", "test-secret")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("RAW_DIR", str(raw_dir))

    # Reload the server module so module-level env-driven state is fresh.
    import importlib
    import src.ingest_server as srv
    importlib.reload(srv)
    return srv, db_path, raw_dir


@pytest.fixture
def client(configured_env) -> TestClient:
    srv, _, _ = configured_env
    return TestClient(srv.app)


def _sample_record(uid: str = "rec-1", value: float = 42.5) -> dict:
    return {
        "uid": uid,
        "type": "HeartRateVariabilityRmssd",
        "start_time": "2026-04-16T03:15:23+00:00",
        "end_time": "2026-04-16T03:15:23+00:00",
        "value": value,
        "unit": "ms",
        "source_app": "com.google.android.apps.fitness",
        "source_device": "Pixel Watch 3",
        "metadata": {},
    }


def _batch(records: list[dict], batch_id: str = "test-batch-1") -> dict:
    return {
        "batch_id": batch_id,
        "synced_at": "2026-04-16T10:00:00+00:00",
        "records": records,
    }


class TestHealthcheck:
    def test_health_is_unauthenticated(self, client: TestClient):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True}


class TestAuth:
    def test_missing_token_401(self, client: TestClient):
        r = client.post("/ingest/health-connect", json=_batch([_sample_record()]))
        assert r.status_code == 401

    def test_wrong_token_401(self, client: TestClient):
        r = client.post(
            "/ingest/health-connect",
            json=_batch([_sample_record()]),
            headers={"X-Auth-Token": "wrong"},
        )
        assert r.status_code == 401


class TestIngestHappyPath:
    def test_single_record_accepted(self, client: TestClient, configured_env):
        srv, db_path, raw_dir = configured_env
        r = client.post(
            "/ingest/health-connect",
            json=_batch([_sample_record("rec-single")]),
            headers={"X-Auth-Token": "test-secret"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"ok": True, "accepted": 1, "duplicates": 0}

        # Row visible in DB
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT uid, type, value FROM hc_records WHERE uid=?",
                ("rec-single",),
            ).fetchone()
            assert row is not None
            assert row[0] == "rec-single"
            assert row[1] == "HeartRateVariabilityRmssd"
            assert row[2] == 42.5
        finally:
            conn.close()

        # Raw batch archived
        raw_files = list((raw_dir / "health_connect").rglob("batch_*.json"))
        assert len(raw_files) == 1
        archived = json.loads(raw_files[0].read_text(encoding="utf-8"))
        assert archived["batch_id"] == "test-batch-1"
        assert len(archived["records"]) == 1

    def test_local_date_derived_from_start_time(self, client: TestClient, configured_env):
        _, db_path, _ = configured_env
        rec = _sample_record("rec-date")
        rec["start_time"] = "2026-04-16T03:15:23+00:00"  # 03:15 UTC = local-dependent
        client.post(
            "/ingest/health-connect",
            json=_batch([rec]),
            headers={"X-Auth-Token": "test-secret"},
        )
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT date FROM hc_records WHERE uid='rec-date'"
            ).fetchone()
            assert row is not None
            # Date is the server-local calendar date for that instant.
            # Don't hardcode expected (tz varies by CI); assert format only.
            assert row[0].count("-") == 2
            assert len(row[0]) == 10
        finally:
            conn.close()


class TestIngestDedup:
    def test_duplicate_uid_is_noop(self, client: TestClient, configured_env):
        srv, db_path, _ = configured_env
        rec = _sample_record("rec-dup")

        first = client.post(
            "/ingest/health-connect",
            json=_batch([rec], batch_id="b1"),
            headers={"X-Auth-Token": "test-secret"},
        )
        assert first.status_code == 200
        assert first.json() == {"ok": True, "accepted": 1, "duplicates": 0}

        second = client.post(
            "/ingest/health-connect",
            json=_batch([rec], batch_id="b2"),
            headers={"X-Auth-Token": "test-secret"},
        )
        assert second.status_code == 200
        assert second.json() == {"ok": True, "accepted": 0, "duplicates": 1}

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM hc_records WHERE uid='rec-dup'"
            ).fetchone()[0]
            assert cnt == 1
        finally:
            conn.close()

    def test_mixed_batch_counts_both(self, client: TestClient):
        recs = [_sample_record("rec-A"), _sample_record("rec-B")]
        client.post(
            "/ingest/health-connect",
            json=_batch(recs, batch_id="b1"),
            headers={"X-Auth-Token": "test-secret"},
        )
        # Second batch has one new + one duplicate
        recs2 = [_sample_record("rec-B"), _sample_record("rec-C")]
        r = client.post(
            "/ingest/health-connect",
            json=_batch(recs2, batch_id="b2"),
            headers={"X-Auth-Token": "test-secret"},
        )
        assert r.json() == {"ok": True, "accepted": 1, "duplicates": 1}


class TestHealthConnectView:
    def test_view_visible_after_ingest(self, client: TestClient, configured_env):
        _, db_path, _ = configured_env
        records = [
            _sample_record(f"hrv-{i}", value=float(40 + i))
            for i in range(3)
        ]
        client.post(
            "/ingest/health-connect",
            json=_batch(records),
            headers={"X-Auth-Token": "test-secret"},
        )
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT date, avg_rmssd, measurements FROM daily_hc_hrv"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0][2] == 3
            assert 40 <= rows[0][1] <= 43
        finally:
            conn.close()
