"""
Интеграционные тесты src/collector.py.

Реальная SQLite (in-memory), мокнутые HTTP (responses).
Проверяет полный поток: fetch → raw_store → parse → db.
"""

import json
import time
from pathlib import Path

import pytest
import responses as resp_lib

from src.collector import Collector
from src.db import Database
from src.fitbit_client import FitbitClient
from src.raw_store import RawStore

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fitbit"
API = "https://api.fitbit.com"
DATE = "2026-04-15"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture
def tokens_file(tmp_path) -> Path:
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({
        "access_token": "test_token",
        "refresh_token": "test_refresh",
        "expires_at": time.time() + 3600,
    }))
    return path


@pytest.fixture
def fitbit_client(tokens_file) -> FitbitClient:
    return FitbitClient(
        tokens_path=tokens_file,
        client_id="cid",
        client_secret="csecret",
    )


@pytest.fixture
def collector(fitbit_client, db, tmp_path) -> Collector:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    store = RawStore(db.conn, raw_dir)
    return Collector(fitbit_client, db, store)


def _mock_all_endpoints(sleep_fixture="sleep_stages.json"):
    """Регистрирует успешные ответы для всех эндпоинтов."""
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/foods/log/date/{DATE}.json",
        json=_load("nutrition.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/foods/log/water/date/{DATE}.json",
        json=_load("water.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/activities/date/{DATE}.json",
        json=_load("activity.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1.2/user/-/sleep/date/{DATE}.json",
        json=_load(sleep_fixture), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/body/log/weight/date/{DATE}.json",
        json=_load("weight.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1.2/user/-/hrv/date/{DATE}.json",
        json=_load("hrv.json"), status=200)
    # New endpoints — return fixture data where available, 404 otherwise (silently ignored)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/activities/heart/date/{DATE}/1d/1min.json",
        json=_load("heart_rate.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/activities/active-zone-minutes/date/{DATE}.json",
        json=_load("azm.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/br/date/{DATE}.json",
        json=_load("br.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/spo2/date/{DATE}.json",
        json=_load("spo2.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/temp/skin/date/{DATE}.json",
        json=_load("skin_temp.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/cardioscore/date/{DATE}.json",
        json=_load("cardio_score.json"), status=200)
    resp_lib.add(resp_lib.GET,
        f"{API}/1/user/-/devices.json",
        json=[], status=200)


# ===========================================================================
# Полный день — все эндпоинты 200
# ===========================================================================

class TestCollectDayFull:
    @resp_lib.activate
    def test_status_ok(self, collector, db):
        _mock_all_endpoints()
        result = collector.collect_day(DATE)
        assert result.status == "ok"
        assert result.errors == []

    @resp_lib.activate
    def test_nutrition_in_db(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        n = db.get_nutrition(DATE)
        assert n["calories"] == 1190
        assert n["water_ml"] == 1800.0

    @resp_lib.activate
    def test_activity_in_db(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        a = db.get_activity(DATE)
        assert a["steps"] == 8432

    @resp_lib.activate
    def test_sleep_in_db(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        sessions = db.get_sleep_sessions(DATE)
        assert len(sessions) == 1
        assert sessions[0]["deep_minutes"] == 72

    @resp_lib.activate
    def test_sleep_stages_in_db(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        log_id = db.get_sleep_sessions(DATE)[0]["log_id"]
        stages = db.get_sleep_stages(log_id)
        assert len(stages) > 0

    @resp_lib.activate
    def test_weight_in_db(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        assert db.get_weight(DATE)["weight_kg"] == 87.2

    @resp_lib.activate
    def test_hrv_in_db(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        assert db.get_hrv(DATE)["rmssd"] == 42.3

    @resp_lib.activate
    def test_food_log_in_db(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        assert len(db.get_food_log(DATE)) == 3

    @resp_lib.activate
    def test_sync_log_ok(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        status = db.get_sync_status(DATE)
        assert status["status"] == "ok"
        assert status["errors"] is None

    @resp_lib.activate
    def test_raw_files_saved(self, collector, db, tmp_path):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        # Все эндпоинты должны быть в raw_files
        rows = db.conn.execute(
            "SELECT kind FROM raw_files WHERE source='fitbit' AND date=?", (DATE,)
        ).fetchall()
        kinds = {r[0] for r in rows}
        assert kinds == {
            "nutrition", "water", "activity", "sleep", "weight", "hrv",
            "heart_rate", "azm", "br", "spo2", "skin_temp", "cardio_score",
            "devices",
        }


# ===========================================================================
# Частичный провал — один эндпоинт 500
# ===========================================================================

class TestCollectDayPartial:
    @resp_lib.activate
    def test_status_partial_on_one_failure(self, collector, db):
        # sleep возвращает 500, остальные — 200
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/foods/log/date/{DATE}.json",
            json=_load("nutrition.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/foods/log/water/date/{DATE}.json",
            json=_load("water.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/activities/date/{DATE}.json",
            json=_load("activity.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1.2/user/-/sleep/date/{DATE}.json",
            json={"error": "server error"}, status=500)
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/body/log/weight/date/{DATE}.json",
            json=_load("weight.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1.2/user/-/hrv/date/{DATE}.json",
            json=_load("hrv.json"), status=200)

        result = collector.collect_day(DATE)
        assert result.status == "partial"

    @resp_lib.activate
    def test_error_recorded_in_sync_log(self, collector, db):
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/foods/log/date/{DATE}.json",
            json=_load("nutrition.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/foods/log/water/date/{DATE}.json",
            json=_load("water.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/activities/date/{DATE}.json",
            json=_load("activity.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1.2/user/-/sleep/date/{DATE}.json",
            status=500, json={})
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/body/log/weight/date/{DATE}.json",
            json=_load("weight.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1.2/user/-/hrv/date/{DATE}.json",
            json=_load("hrv.json"), status=200)

        collector.collect_day(DATE)
        status = db.get_sync_status(DATE)
        assert status["status"] == "partial"
        assert any("sleep" in e for e in status["errors"])

    @resp_lib.activate
    def test_successful_endpoints_still_saved(self, collector, db):
        """Провал sleep не должен откатывать nutrition/activity."""
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/foods/log/date/{DATE}.json",
            json=_load("nutrition.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/foods/log/water/date/{DATE}.json",
            json=_load("water.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/activities/date/{DATE}.json",
            json=_load("activity.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1.2/user/-/sleep/date/{DATE}.json",
            status=500, json={})
        resp_lib.add(resp_lib.GET,
            f"{API}/1/user/-/body/log/weight/date/{DATE}.json",
            json=_load("weight.json"), status=200)
        resp_lib.add(resp_lib.GET,
            f"{API}/1.2/user/-/hrv/date/{DATE}.json",
            json=_load("hrv.json"), status=200)

        collector.collect_day(DATE)
        assert db.get_nutrition(DATE)["calories"] == 1190
        assert db.get_activity(DATE)["steps"] == 8432
        assert db.get_sleep_sessions(DATE) == []  # sleep не сохранён


# ===========================================================================
# Идемпотентность
# ===========================================================================

class TestCollectDayIdempotent:
    @resp_lib.activate
    def test_second_call_skipped(self, collector, db):
        _mock_all_endpoints()
        _mock_all_endpoints()  # второй набор моков на случай второго вызова
        collector.collect_day(DATE)
        result = collector.collect_day(DATE)
        assert result.status == "skipped"

    @resp_lib.activate
    def test_skipped_makes_only_one_api_call_set(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        calls_after_first = len(resp_lib.calls)

        collector.collect_day(DATE)  # должен пропустить
        assert len(resp_lib.calls) == calls_after_first  # новых запросов нет

    @resp_lib.activate
    def test_force_reruns_despite_ok(self, collector, db):
        _mock_all_endpoints()
        _mock_all_endpoints()
        collector.collect_day(DATE)
        result = collector.collect_day(DATE, force=True)
        assert result.status == "ok"

    @resp_lib.activate
    def test_second_collect_no_duplicate_sessions(self, collector, db):
        _mock_all_endpoints()
        _mock_all_endpoints()
        collector.collect_day(DATE)
        collector.collect_day(DATE, force=True)
        assert len(db.get_sleep_sessions(DATE)) == 1

    @resp_lib.activate
    def test_second_collect_no_duplicate_food_log(self, collector, db):
        _mock_all_endpoints()
        _mock_all_endpoints()
        collector.collect_day(DATE)
        collector.collect_day(DATE, force=True)
        assert len(db.get_food_log(DATE)) == 3  # не 6


# ===========================================================================
# reparse_day
# ===========================================================================

class TestReparseDay:
    @resp_lib.activate
    def test_reparse_without_api_calls(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)
        calls_after_collect = len(resp_lib.calls)

        collector.reparse_day(DATE)
        assert len(resp_lib.calls) == calls_after_collect  # нет новых HTTP-запросов

    @resp_lib.activate
    def test_reparse_updates_db(self, collector, db):
        _mock_all_endpoints()
        collector.collect_day(DATE)

        # Удаляем запись из БД
        db.conn.execute("DELETE FROM daily_activity WHERE date=?", (DATE,))
        db.conn.commit()
        assert db.get_activity(DATE) is None

        collector.reparse_day(DATE)
        assert db.get_activity(DATE)["steps"] == 8432
