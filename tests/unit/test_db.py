"""Тесты для src/db.py."""

import json

import pytest


# ===========================================================================
# Nutrition
# ===========================================================================

class TestNutrition:
    def test_save_and_get(self, db):
        db.save_nutrition("2026-04-15", calories=1842, protein_g=98.0, fat_g=72.0,
                          carbs_g=184.0, fiber_g=25.0, water_ml=1800.0)
        row = db.get_nutrition("2026-04-15")
        assert row["calories"] == 1842
        assert row["protein_g"] == 98.0
        assert row["water_ml"] == 1800.0

    def test_upsert_overwrites(self, db):
        db.save_nutrition("2026-04-15", calories=1000)
        db.save_nutrition("2026-04-15", calories=2000)
        assert db.get_nutrition("2026-04-15")["calories"] == 2000

    def test_returns_none_for_missing_date(self, db):
        assert db.get_nutrition("2020-01-01") is None

    def test_partial_fields_allowed(self, db):
        db.save_nutrition("2026-04-15", calories=500)
        row = db.get_nutrition("2026-04-15")
        assert row["calories"] == 500
        assert row["protein_g"] is None


# ===========================================================================
# Activity
# ===========================================================================

class TestActivity:
    def test_save_and_get(self, db):
        db.save_activity("2026-04-15", steps=8432, distance_km=5.2,
                         active_minutes_very=47)
        row = db.get_activity("2026-04-15")
        assert row["steps"] == 8432
        assert row["distance_km"] == 5.2
        assert row["active_minutes_very"] == 47

    def test_upsert(self, db):
        db.save_activity("2026-04-15", steps=1000)
        db.save_activity("2026-04-15", steps=9000)
        assert db.get_activity("2026-04-15")["steps"] == 9000

    def test_missing_returns_none(self, db):
        assert db.get_activity("2020-01-01") is None


# ===========================================================================
# Sleep sessions + stages
# ===========================================================================

class TestSleep:
    def test_save_stages_sleep_session(self, db):
        db.save_sleep_session(
            log_id=123, date_of_sleep="2026-04-15",
            start_time="2026-04-14T23:00", end_time="2026-04-15T07:00",
            duration_minutes=480, efficiency=89,
            sleep_type="stages",
            deep_minutes=72, light_minutes=250, rem_minutes=105, wake_minutes=53,
        )
        sessions = db.get_sleep_sessions("2026-04-15")
        assert len(sessions) == 1
        assert sessions[0]["log_id"] == 123
        assert sessions[0]["deep_minutes"] == 72

    def test_multiple_sessions_same_day(self, db):
        db.save_sleep_session(123, "2026-04-15", "23:00", "07:00",
                              is_main_sleep=True)
        db.save_sleep_session(456, "2026-04-15", "14:00", "15:30",
                              is_main_sleep=False)
        sessions = db.get_sleep_sessions("2026-04-15")
        assert len(sessions) == 2

    def test_no_sessions_returns_empty(self, db):
        assert db.get_sleep_sessions("2020-01-01") == []

    def test_save_and_get_stages(self, db):
        db.save_sleep_session(123, "2026-04-15", "23:00", "07:00")
        stages = [
            {"date_time": "2026-04-14T23:00:00", "level": "light", "seconds": 30},
            {"date_time": "2026-04-14T23:00:30", "level": "deep",  "seconds": 30},
            {"date_time": "2026-04-14T23:01:00", "level": "rem",   "seconds": 30},
        ]
        db.save_sleep_stages(123, stages)
        result = db.get_sleep_stages(123)
        assert len(result) == 3
        assert result[0]["level"] == "light"

    def test_short_data_flag(self, db):
        db.save_sleep_session(123, "2026-04-15", "23:00", "07:00")
        stages = [
            {"date_time": "2026-04-15T03:00:00", "level": "wake",
             "seconds": 60, "is_short": True},
        ]
        db.save_sleep_stages(123, stages)
        result = db.get_sleep_stages(123)
        assert result[0]["is_short"] == 1

    def test_save_stages_replaces_existing(self, db):
        db.save_sleep_session(123, "2026-04-15", "23:00", "07:00")
        db.save_sleep_stages(123, [
            {"date_time": "T1", "level": "light", "seconds": 30},
        ])
        db.save_sleep_stages(123, [
            {"date_time": "T1", "level": "deep", "seconds": 30},
            {"date_time": "T2", "level": "rem",  "seconds": 30},
        ])
        result = db.get_sleep_stages(123)
        assert len(result) == 2
        assert result[0]["level"] == "deep"

    def test_batch_insert_900_stages(self, db):
        """Batch-вставка ~900 строк (типичная ночь) без ошибок."""
        db.save_sleep_session(999, "2026-04-15", "22:00", "06:00")
        stages = [
            {"date_time": f"2026-04-15T00:{i//2:02d}:{(i%2)*30:02d}",
             "level": ["light", "deep", "rem", "wake"][i % 4],
             "seconds": 30}
            for i in range(900)
        ]
        db.save_sleep_stages(999, stages)
        assert len(db.get_sleep_stages(999)) == 900

    def test_classic_sleep_fields(self, db):
        db.save_sleep_session(
            200, "2026-04-15", "23:30", "07:30",
            sleep_type="classic",
            asleep_minutes=400, restless_minutes=30, awake_minutes=10,
        )
        s = db.get_sleep_sessions("2026-04-15")[0]
        assert s["sleep_type"] == "classic"
        assert s["asleep_minutes"] == 400


# ===========================================================================
# Weight + HRV
# ===========================================================================

class TestWeightHrv:
    def test_save_weight(self, db):
        db.save_weight("2026-04-15", weight_kg=87.2, bmi=26.5, fat_percent=22.0)
        row = db.get_weight("2026-04-15")
        assert row["weight_kg"] == 87.2

    def test_save_hrv(self, db):
        db.save_hrv("2026-04-15", rmssd=42.0, coverage=0.95)
        row = db.get_hrv("2026-04-15")
        assert row["rmssd"] == 42.0
        assert row["coverage"] == 0.95

    def test_weight_upsert(self, db):
        db.save_weight("2026-04-15", weight_kg=90.0)
        db.save_weight("2026-04-15", weight_kg=88.0)
        assert db.get_weight("2026-04-15")["weight_kg"] == 88.0


# ===========================================================================
# Food log
# ===========================================================================

class TestFoodLog:
    def test_save_and_get(self, db):
        entries = [
            {"meal_type": "Breakfast", "food_name": "Oatmeal",
             "calories": 300, "protein_g": 10.0},
            {"meal_type": "Lunch", "food_name": "Chicken",
             "calories": 450, "protein_g": 45.0},
        ]
        db.save_food_log("2026-04-15", entries)
        result = db.get_food_log("2026-04-15")
        assert len(result) == 2
        assert result[0]["food_name"] == "Oatmeal"

    def test_replaces_existing_entries(self, db):
        db.save_food_log("2026-04-15", [{"food_name": "Old", "calories": 100}])
        db.save_food_log("2026-04-15", [{"food_name": "New", "calories": 200}])
        result = db.get_food_log("2026-04-15")
        assert len(result) == 1
        assert result[0]["food_name"] == "New"

    def test_empty_entries(self, db):
        db.save_food_log("2026-04-15", [])
        assert db.get_food_log("2026-04-15") == []


# ===========================================================================
# CPAP
# ===========================================================================

class TestCpap:
    def test_save_and_get_session(self, db):
        db.save_cpap_session(
            "2026-04-15",
            duration_minutes=412,
            ahi=3.2,
            leak_median=4.1,
            pressure_median=10.2,
        )
        row = db.get_cpap_session("2026-04-15")
        assert row["ahi"] == 3.2
        assert row["duration_minutes"] == 412

    def test_missing_returns_none(self, db):
        assert db.get_cpap_session("2020-01-01") is None

    def test_save_and_get_events(self, db):
        events = [
            {"timestamp": "2026-04-15T01:00:00", "event_type": "obstructive",
             "duration_seconds": 12.5},
            {"timestamp": "2026-04-15T02:00:00", "event_type": "central",
             "duration_seconds": 8.0},
        ]
        db.save_cpap_events("2026-04-15", events)
        result = db.get_cpap_events("2026-04-15")
        assert len(result) == 2
        assert result[0]["event_type"] == "obstructive"

    def test_events_replace_on_rewrite(self, db):
        db.save_cpap_events("2026-04-15", [
            {"timestamp": "T1", "event_type": "central"},
        ])
        db.save_cpap_events("2026-04-15", [
            {"timestamp": "T2", "event_type": "obstructive"},
            {"timestamp": "T3", "event_type": "hypopnea"},
        ])
        result = db.get_cpap_events("2026-04-15")
        assert len(result) == 2


# ===========================================================================
# O2Ring
# ===========================================================================

class TestO2Ring:
    def test_save_and_get_session(self, db):
        session_id = db.save_o2ring_session(
            "2026-04-15", "22:00", "06:00",
            avg_spo2=95.0, min_spo2=88.0, spo2_drops_count=7,
        )
        row = db.get_o2ring_session("2026-04-15")
        assert row["avg_spo2"] == 95.0
        assert row["min_spo2"] == 88.0
        assert session_id > 0

    def test_save_and_get_data(self, db):
        sid = db.save_o2ring_session("2026-04-15", "22:00", "06:00")
        data = [
            {"timestamp": "2026-04-15T22:00:00", "spo2": 97, "heart_rate": 62},
            {"timestamp": "2026-04-15T22:00:04", "spo2": 96, "heart_rate": 61},
        ]
        db.save_o2ring_data(sid, data)
        result = db.get_o2ring_data(sid)
        assert len(result) == 2
        assert result[0]["spo2"] == 97

    def test_data_replaces_on_rewrite(self, db):
        sid = db.save_o2ring_session("2026-04-15", "22:00", "06:00")
        db.save_o2ring_data(sid, [{"timestamp": "T1", "spo2": 95}])
        db.save_o2ring_data(sid, [
            {"timestamp": "T2", "spo2": 93},
            {"timestamp": "T3", "spo2": 94},
        ])
        assert len(db.get_o2ring_data(sid)) == 2

    def test_batch_insert_7200_rows(self, db):
        """Batch-вставка ~7200 строк (типичная ночь 4-сек данных) без ошибок."""
        sid = db.save_o2ring_session("2026-04-15", "22:00", "06:00")
        data = [
            {"timestamp": f"2026-04-15T{h:02d}:{m:02d}:{s:02d}",
             "spo2": 95, "heart_rate": 60}
            for h in range(22, 30)
            for m in range(60)
            for s in range(0, 60, 4)
            if len([1]) <= 7200
        ]
        data = data[:7200]
        db.save_o2ring_data(sid, data)
        assert len(db.get_o2ring_data(sid)) == 7200


# ===========================================================================
# Sync log
# ===========================================================================

class TestSyncLog:
    def test_is_date_synced_ok(self, db):
        db.upsert_sync_log("2026-04-15", "ok")
        assert db.is_date_synced("fitbit", "2026-04-15") is True

    def test_is_date_synced_partial(self, db):
        db.upsert_sync_log("2026-04-15", "partial", errors=["hrv failed"])
        assert db.is_date_synced("fitbit", "2026-04-15") is False

    def test_is_date_synced_error(self, db):
        db.upsert_sync_log("2026-04-15", "error")
        assert db.is_date_synced("fitbit", "2026-04-15") is False

    def test_is_date_synced_missing(self, db):
        assert db.is_date_synced("fitbit", "2020-01-01") is False

    def test_get_sync_status_with_errors(self, db):
        db.upsert_sync_log("2026-04-15", "partial", errors=["hrv: 500", "weight: 429"])
        status = db.get_sync_status("2026-04-15")
        assert status["status"] == "partial"
        assert "hrv: 500" in status["errors"]

    def test_upsert_sync_log_overwrites(self, db):
        db.upsert_sync_log("2026-04-15", "error")
        db.upsert_sync_log("2026-04-15", "ok")
        assert db.is_date_synced("fitbit", "2026-04-15") is True


# ===========================================================================
# Агрегированные методы
# ===========================================================================

class TestGetDay:
    def test_returns_all_keys(self, db):
        day = db.get_day("2020-01-01")
        assert set(day.keys()) == {
            "date", "nutrition", "activity", "sleep",
            "weight", "hrv", "cpap", "o2ring",
            "food_log", "health_metrics", "heart_rate", "azm", "activity_log",
            "sync_status",
        }

    def test_empty_day_has_none_and_lists(self, db):
        day = db.get_day("2020-01-01")
        assert day["nutrition"] is None
        assert day["sleep"] == []

    def test_populated_day(self, db):
        db.save_nutrition("2026-04-15", calories=1842)
        db.save_activity("2026-04-15", steps=8432)
        day = db.get_day("2026-04-15")
        assert day["nutrition"]["calories"] == 1842
        assert day["activity"]["steps"] == 8432


class TestGetRange:
    def test_nutrition_range(self, db):
        for d, cal in [("2026-04-13", 1500), ("2026-04-15", 1800),
                       ("2026-04-17", 2000)]:
            db.save_nutrition(d, calories=cal)
        rows = db.get_range("nutrition", "2026-04-14", "2026-04-16")
        assert len(rows) == 1
        assert rows[0]["calories"] == 1800

    def test_unknown_metric_raises(self, db):
        with pytest.raises(ValueError, match="Unknown metric"):
            db.get_range("unknown", "2026-04-01", "2026-04-30")

    def test_empty_range_returns_empty(self, db):
        assert db.get_range("nutrition", "2020-01-01", "2020-01-31") == []


class TestGetLatest:
    def test_returns_most_recent_date(self, db):
        db.save_nutrition("2026-04-10", calories=1000)
        db.save_nutrition("2026-04-15", calories=1500)
        assert db.get_latest("nutrition") == "2026-04-15"

    def test_returns_none_when_empty(self, db):
        assert db.get_latest("nutrition") is None

    def test_unknown_metric_raises(self, db):
        with pytest.raises(ValueError):
            db.get_latest("unknown")
