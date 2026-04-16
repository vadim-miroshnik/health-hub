"""
Юнит-тесты расширенных парсеров src/collector.py.

Нет HTTP-запросов — все данные из fixtures/fitbit/*.json.
"""

import json
from pathlib import Path

import pytest

from src.collector import (
    parse_azm,
    parse_br,
    parse_cardio_score,
    parse_heart_rate,
    parse_skin_temp,
    parse_spo2,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fitbit"
DATE = "2026-04-15"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ===========================================================================
# Heart Rate
# ===========================================================================

class TestParseHeartRate:
    def test_saves_resting_hr(self, db):
        parse_heart_rate(DATE, _load("heart_rate.json"), db)
        hr = db.get_heart_rate(DATE)
        assert hr is not None
        assert hr["resting_hr"] == 58

    def test_saves_zones(self, db):
        parse_heart_rate(DATE, _load("heart_rate.json"), db)
        hr = db.get_heart_rate(DATE)
        assert hr["fat_burn_minutes"] == 312
        assert hr["cardio_minutes"] == 187
        assert hr["peak_minutes"] == 49
        assert hr["out_of_range_minutes"] == 892

    def test_saves_zone_calories(self, db):
        parse_heart_rate(DATE, _load("heart_rate.json"), db)
        hr = db.get_heart_rate(DATE)
        assert hr["fat_burn_calories"] == 892.1
        assert hr["cardio_calories"] == 634.5
        assert hr["peak_calories"] == 198.3
        assert hr["out_of_range_calories"] == 1423.2

    def test_saves_intraday(self, db):
        parse_heart_rate(DATE, _load("heart_rate.json"), db)
        entries = db.get_hr_intraday(DATE)
        assert len(entries) == 3
        assert entries[0]["bpm"] == 62
        assert entries[0]["time"] == "00:01:00"

    def test_empty_response_no_crash(self, db):
        parse_heart_rate(DATE, {"activities-heart": []}, db)
        assert db.get_heart_rate(DATE) is None

    def test_empty_response_no_intraday(self, db):
        parse_heart_rate(DATE, {"activities-heart": []}, db)
        assert db.get_hr_intraday(DATE) == []


# ===========================================================================
# AZM
# ===========================================================================

class TestParseAzm:
    def test_saves_totals(self, db):
        parse_azm(DATE, _load("azm.json"), db)
        azm = db.get_azm(DATE)
        assert azm is not None
        assert azm["total_minutes"] == 55  # 32 + 18 + 5

    def test_saves_zones(self, db):
        parse_azm(DATE, _load("azm.json"), db)
        azm = db.get_azm(DATE)
        assert azm["fat_burn_minutes"] == 32
        assert azm["cardio_minutes"] == 18
        assert azm["peak_minutes"] == 5

    def test_empty_response_no_crash(self, db):
        parse_azm(DATE, {"activities-active-zone-minutes": []}, db)
        assert db.get_azm(DATE) is None


# ===========================================================================
# Breathing Rate
# ===========================================================================

class TestParseBr:
    def test_saves_breathing_rate(self, db):
        parse_br(DATE, _load("br.json"), db)
        hm = db.get_health_metrics(DATE)
        assert hm is not None
        assert hm["breathing_rate"] == 14.8

    def test_empty_response_no_crash(self, db):
        parse_br(DATE, {"br": []}, db)
        assert db.get_health_metrics(DATE) is None


# ===========================================================================
# SpO2
# ===========================================================================

class TestParseSpo2:
    def test_saves_avg_and_min(self, db):
        parse_spo2(DATE, _load("spo2.json"), db)
        hm = db.get_health_metrics(DATE)
        assert hm is not None
        assert hm["spo2_avg"] == 95.4
        assert hm["spo2_min"] == 91.0

    def test_empty_value_no_crash(self, db):
        parse_spo2(DATE, {"value": {}}, db)
        assert db.get_health_metrics(DATE) is None


# ===========================================================================
# Skin Temperature
# ===========================================================================

class TestParseSkinTemp:
    def test_saves_delta(self, db):
        parse_skin_temp(DATE, _load("skin_temp.json"), db)
        hm = db.get_health_metrics(DATE)
        assert hm is not None
        assert hm["skin_temp_delta"] == pytest.approx(-0.12)

    def test_empty_response_no_crash(self, db):
        parse_skin_temp(DATE, {"tempSkin": []}, db)
        assert db.get_health_metrics(DATE) is None


# ===========================================================================
# Cardio Score
# ===========================================================================

class TestParseCardioScore:
    def test_saves_range(self, db):
        parse_cardio_score(DATE, _load("cardio_score.json"), db)
        hm = db.get_health_metrics(DATE)
        assert hm is not None
        assert hm["cardio_score_min"] == 42.0
        assert hm["cardio_score_max"] == 46.0

    def test_empty_response_no_crash(self, db):
        parse_cardio_score(DATE, {"cardioScore": []}, db)
        assert db.get_health_metrics(DATE) is None


# ===========================================================================
# Health metrics COALESCE — multiple parsers same date
# ===========================================================================

class TestHealthMetricsMerge:
    def test_br_then_spo2_preserves_both(self, db):
        """Save br first, then spo2 — neither should overwrite the other."""
        parse_br(DATE, _load("br.json"), db)
        parse_spo2(DATE, _load("spo2.json"), db)
        hm = db.get_health_metrics(DATE)
        assert hm["breathing_rate"] == 14.8
        assert hm["spo2_avg"] == 95.4
        assert hm["spo2_min"] == 91.0

    def test_all_health_metrics_accumulate(self, db):
        parse_br(DATE, _load("br.json"), db)
        parse_spo2(DATE, _load("spo2.json"), db)
        parse_skin_temp(DATE, _load("skin_temp.json"), db)
        parse_cardio_score(DATE, _load("cardio_score.json"), db)
        hm = db.get_health_metrics(DATE)
        assert hm["breathing_rate"] == 14.8
        assert hm["spo2_avg"] == 95.4
        assert hm["skin_temp_delta"] == pytest.approx(-0.12)
        assert hm["cardio_score_min"] == 42.0
        assert hm["cardio_score_max"] == 46.0
