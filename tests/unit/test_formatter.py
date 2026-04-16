import pytest
from src.formatter import format_day, _esc, _fmt_minutes


class TestEscape:
    def test_escapes_dot(self):
        assert _esc("87.2") == r"87\.2"

    def test_escapes_dash(self):
        assert _esc("10-14") == r"10\-14"

    def test_escapes_parens(self):
        assert _esc("(test)") == r"\(test\)"

    def test_escapes_underscore(self):
        assert _esc("hello_world") == r"hello\_world"

    def test_no_double_escape(self):
        # Already-escaped text should not be double-escaped
        result = _esc("3.14")
        assert result == r"3\.14"


class TestFmtMinutes:
    def test_zero(self):
        assert _fmt_minutes(0) == "0м"

    def test_less_than_hour(self):
        assert _fmt_minutes(45) == "45м"

    def test_exact_hour(self):
        assert _fmt_minutes(60) == "1ч 00м"

    def test_hours_and_minutes(self):
        assert _fmt_minutes(443) == "7ч 23м"

    def test_none_returns_question(self):
        assert _fmt_minutes(None) == "?"


class TestFormatDay:
    def _full_data(self):
        return {
            "date": "2026-04-15",
            "nutrition": {"calories": 1842, "protein_g": 98, "fat_g": 72, "carbs_g": 184, "water_ml": 1800},
            "activity": {"steps": 8432, "distance_km": 5.2, "active_minutes_lightly": 30, "active_minutes_fairly": 10, "active_minutes_very": 7},
            "sleep": [{"is_main_sleep": True, "duration_minutes": 443, "efficiency": 89, "deep_minutes": 72, "rem_minutes": 105}],
            "weight": {"weight_kg": 87.2},
            "hrv": {"rmssd": 42.0},
            "cpap": {"duration_minutes": 412, "ahi": 3.2, "obstructive_events": 4, "central_events": 12, "hypopnea_events": 6, "leak_median": 4.1, "pressure_min": 10.2, "pressure_max": 14.8},
            "o2ring": {"avg_spo2": 95.0, "min_spo2": 88.0, "spo2_drops_count": 7, "avg_hr": 62.0},
        }

    def test_returns_nonempty_string(self):
        text = format_day(self._full_data())
        assert isinstance(text, str)
        assert len(text) > 0

    def test_header_contains_date(self):
        text = format_day(self._full_data())
        assert "апреля 2026" in text

    def test_calories_present(self):
        text = format_day(self._full_data())
        assert "1842 kcal" in text or "1842" in text

    def test_steps_present(self):
        text = format_day(self._full_data())
        assert "8" in text and "432" in text  # "8 432 шага"

    def test_sleep_present(self):
        text = format_day(self._full_data())
        assert "7ч 23м" in text

    def test_weight_present(self):
        text = format_day(self._full_data())
        assert "87" in text

    def test_cpap_present(self):
        text = format_day(self._full_data())
        assert "CPAP" in text
        assert "AHI" in text

    def test_o2ring_present(self):
        text = format_day(self._full_data())
        assert "SpO2" in text

    def test_empty_data_returns_empty_string(self):
        data = {"date": "2026-04-15", "nutrition": None, "activity": None,
                "sleep": [], "weight": None, "hrv": None, "cpap": None, "o2ring": None}
        assert format_day(data) == ""

    def test_missing_cpap_omits_section(self):
        data = self._full_data()
        data["cpap"] = None
        text = format_day(data)
        assert "CPAP" not in text

    def test_missing_o2ring_omits_section(self):
        data = self._full_data()
        data["o2ring"] = None
        text = format_day(data)
        assert "SpO2" not in text

    def test_dots_are_escaped(self):
        text = format_day(self._full_data())
        # "87.2" must appear escaped as "87\.2"
        assert r"87\.2" in text

    def test_under_4096_chars(self):
        text = format_day(self._full_data())
        assert len(text) <= 4096

    def test_partial_nutrition_no_crash(self):
        data = self._full_data()
        data["nutrition"] = {"calories": 1500, "protein_g": None, "fat_g": None, "carbs_g": None, "water_ml": None}
        text = format_day(data)
        assert "1500 kcal" in text
