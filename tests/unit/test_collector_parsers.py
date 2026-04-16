"""
Юнит-тесты парсеров src/collector.py.

Нет HTTP-запросов — все данные из fixtures/fitbit/*.json.
"""

import json
from pathlib import Path

import pytest

from src.collector import (
    parse_activity,
    parse_hrv,
    parse_nutrition,
    parse_sleep,
    parse_water,
    parse_weight,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fitbit"
DATE = "2026-04-15"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ===========================================================================
# Sleep — stages
# ===========================================================================

class TestParseSleepStages:
    def test_session_saved(self, db):
        parse_sleep(DATE, _load("sleep_stages.json"), db)
        sessions = db.get_sleep_sessions(DATE)
        assert len(sessions) == 1
        s = sessions[0]
        assert s["log_id"] == 40001
        assert s["sleep_type"] == "stages"
        assert s["is_main_sleep"]

    def test_stage_minutes(self, db):
        parse_sleep(DATE, _load("sleep_stages.json"), db)
        s = db.get_sleep_sessions(DATE)[0]
        assert s["deep_minutes"] == 72
        assert s["light_minutes"] == 250
        assert s["rem_minutes"] == 105
        assert s["wake_minutes"] == 25
        # classic-only fields должны быть None
        assert s["asleep_minutes"] is None

    def test_duration_converted_from_ms(self, db):
        parse_sleep(DATE, _load("sleep_stages.json"), db)
        s = db.get_sleep_sessions(DATE)[0]
        # 28320000 ms / 60000 = 472 мин
        assert s["duration_minutes"] == 472

    def test_stages_saved(self, db):
        parse_sleep(DATE, _load("sleep_stages.json"), db)
        log_id = db.get_sleep_sessions(DATE)[0]["log_id"]
        stages = db.get_sleep_stages(log_id)
        assert len(stages) > 0

    def test_shortdata_flag(self, db):
        parse_sleep(DATE, _load("sleep_stages.json"), db)
        log_id = db.get_sleep_sessions(DATE)[0]["log_id"]
        stages = db.get_sleep_stages(log_id)
        short = [s for s in stages if s["is_short"]]
        # Фикстура содержит 2 shortData записи
        assert len(short) == 2

    def test_shortdata_level(self, db):
        parse_sleep(DATE, _load("sleep_stages.json"), db)
        log_id = db.get_sleep_sessions(DATE)[0]["log_id"]
        stages = db.get_sleep_stages(log_id)
        short_levels = {s["level"] for s in stages if s["is_short"]}
        assert short_levels == {"wake"}

    def test_regular_stages_not_short(self, db):
        parse_sleep(DATE, _load("sleep_stages.json"), db)
        log_id = db.get_sleep_sessions(DATE)[0]["log_id"]
        stages = db.get_sleep_stages(log_id)
        regular = [s for s in stages if not s["is_short"]]
        assert len(regular) == 12  # data[] в фикстуре


# ===========================================================================
# Sleep — classic
# ===========================================================================

class TestParseSleepClassic:
    def test_session_type(self, db):
        parse_sleep(DATE, _load("sleep_classic.json"), db)
        s = db.get_sleep_sessions(DATE)[0]
        assert s["sleep_type"] == "classic"

    def test_classic_minutes(self, db):
        parse_sleep(DATE, _load("sleep_classic.json"), db)
        s = db.get_sleep_sessions(DATE)[0]
        assert s["asleep_minutes"] == 390
        assert s["restless_minutes"] == 42
        assert s["awake_minutes"] == 18
        # stages-only fields должны быть None
        assert s["deep_minutes"] is None
        assert s["rem_minutes"] is None

    def test_classic_stages_saved(self, db):
        parse_sleep(DATE, _load("sleep_classic.json"), db)
        log_id = db.get_sleep_sessions(DATE)[0]["log_id"]
        stages = db.get_sleep_stages(log_id)
        # Фикстура содержит 6 записей в data[], shortData отсутствует
        assert len(stages) == 6

    def test_no_shortdata_in_classic(self, db):
        parse_sleep(DATE, _load("sleep_classic.json"), db)
        log_id = db.get_sleep_sessions(DATE)[0]["log_id"]
        stages = db.get_sleep_stages(log_id)
        assert not any(s["is_short"] for s in stages)


# ===========================================================================
# Sleep — multiple sessions (main + nap)
# ===========================================================================

class TestParseSleepMulti:
    def test_both_sessions_saved(self, db):
        parse_sleep(DATE, _load("sleep_multi.json"), db)
        sessions = db.get_sleep_sessions(DATE)
        assert len(sessions) == 2

    def test_main_sleep_flag(self, db):
        parse_sleep(DATE, _load("sleep_multi.json"), db)
        sessions = db.get_sleep_sessions(DATE)
        main = [s for s in sessions if s["is_main_sleep"]]
        nap  = [s for s in sessions if not s["is_main_sleep"]]
        assert len(main) == 1
        assert len(nap) == 1

    def test_each_session_has_own_stages(self, db):
        parse_sleep(DATE, _load("sleep_multi.json"), db)
        sessions = db.get_sleep_sessions(DATE)
        for s in sessions:
            stages = db.get_sleep_stages(s["log_id"])
            assert len(stages) > 0

    def test_shortdata_only_in_main(self, db):
        """Нэп в фикстуре не имеет shortData."""
        parse_sleep(DATE, _load("sleep_multi.json"), db)
        sessions = db.get_sleep_sessions(DATE)
        nap = next(s for s in sessions if not s["is_main_sleep"])
        stages = db.get_sleep_stages(nap["log_id"])
        assert not any(s["is_short"] for s in stages)


# ===========================================================================
# Sleep — empty response
# ===========================================================================

class TestParseSleepEmpty:
    def test_no_crash(self, db):
        parse_sleep(DATE, _load("sleep_empty.json"), db)  # не должно падать

    def test_no_sessions_saved(self, db):
        parse_sleep(DATE, _load("sleep_empty.json"), db)
        assert db.get_sleep_sessions(DATE) == []


# ===========================================================================
# Nutrition
# ===========================================================================

class TestParseNutrition:
    def test_daily_summary_saved(self, db):
        parse_nutrition(DATE, _load("nutrition.json"), db)
        n = db.get_nutrition(DATE)
        assert n is not None
        assert n["calories"] == 1190
        assert n["protein_g"] == 93.0
        assert n["fat_g"] == 19.0
        assert n["carbs_g"] == 165.0
        assert n["fiber_g"] == 13.0

    def test_food_log_saved(self, db):
        parse_nutrition(DATE, _load("nutrition.json"), db)
        foods = db.get_food_log(DATE)
        assert len(foods) == 3

    def test_meal_type_mapping(self, db):
        parse_nutrition(DATE, _load("nutrition.json"), db)
        foods = db.get_food_log(DATE)
        meal_types = {f["food_name"]: f["meal_type"] for f in foods}
        assert meal_types["Oatmeal"] == "Breakfast"
        assert meal_types["Chicken breast"] == "Lunch"
        assert meal_types["Buckwheat"] == "Dinner"

    def test_food_macros(self, db):
        parse_nutrition(DATE, _load("nutrition.json"), db)
        foods = db.get_food_log(DATE)
        oatmeal = next(f for f in foods if f["food_name"] == "Oatmeal")
        assert oatmeal["protein_g"] == 13.0
        assert oatmeal["calories"] == 350

    def test_empty_foods_list(self, db):
        parse_nutrition(DATE, {"foods": [], "summary": {"calories": 0}}, db)
        assert db.get_food_log(DATE) == []


# ===========================================================================
# Water
# ===========================================================================

class TestParseWater:
    def test_water_saved(self, db):
        parse_water(DATE, _load("water.json"), db)
        n = db.get_nutrition(DATE)
        assert n is not None
        assert n["water_ml"] == 1800.0

    def test_water_updates_existing_nutrition(self, db):
        parse_nutrition(DATE, _load("nutrition.json"), db)
        parse_water(DATE, _load("water.json"), db)
        n = db.get_nutrition(DATE)
        assert n["calories"] == 1190   # не затёрто
        assert n["water_ml"] == 1800.0

    def test_zero_water_saved(self, db):
        parse_water(DATE, {"summary": {"water": 0}}, db)
        n = db.get_nutrition(DATE)
        assert n["water_ml"] == 0.0


# ===========================================================================
# Activity
# ===========================================================================

class TestParseActivity:
    def test_steps_and_distance(self, db):
        parse_activity(DATE, _load("activity.json"), db)
        a = db.get_activity(DATE)
        assert a["steps"] == 8432
        assert a["distance_km"] == 5.24

    def test_active_minutes(self, db):
        parse_activity(DATE, _load("activity.json"), db)
        a = db.get_activity(DATE)
        assert a["active_minutes_lightly"] == 185
        assert a["active_minutes_fairly"] == 35
        assert a["active_minutes_very"] == 47
        assert a["sedentary_minutes"] == 721

    def test_calories_and_floors(self, db):
        parse_activity(DATE, _load("activity.json"), db)
        a = db.get_activity(DATE)
        assert a["calories_burned"] == 2524
        assert a["floors"] == 10

    def test_empty_distances(self, db):
        parse_activity(DATE, {"summary": {"steps": 5000, "distances": []}}, db)
        a = db.get_activity(DATE)
        assert a["steps"] == 5000
        assert a["distance_km"] is None


# ===========================================================================
# Weight
# ===========================================================================

class TestParseWeight:
    def test_weight_saved(self, db):
        parse_weight(DATE, _load("weight.json"), db)
        w = db.get_weight(DATE)
        assert w["weight_kg"] == 87.2
        assert w["bmi"] == 26.54
        assert w["fat_percent"] == 22.1

    def test_empty_weight_no_crash(self, db):
        parse_weight(DATE, {"weight": []}, db)
        assert db.get_weight(DATE) is None


# ===========================================================================
# HRV
# ===========================================================================

class TestParseHrv:
    def test_hrv_saved(self, db):
        parse_hrv(DATE, _load("hrv.json"), db)
        h = db.get_hrv(DATE)
        assert h["rmssd"] == 42.3
        assert h["coverage"] == 0.94
        assert h["low_freq"] == 1284.7
        assert h["high_freq"] == 742.3

    def test_empty_hrv_no_crash(self, db):
        parse_hrv(DATE, {"hrv": []}, db)
        assert db.get_hrv(DATE) is None
