"""
P0.3: MarkdownV2-hostile-character coverage for formatter fields.

Every numeric field that flows into Telegram must either go through _esc() or
be sanitised upstream. This test enumerates the field-by-field contract so a
future refactor that forgets to escape a '.' or '-' fails here instead of in
Telegram with a 400.
"""

import re

from src.formatter import format_day

# MarkdownV2 special chars that MUST be escaped when they appear in literal text.
# Ref: https://core.telegram.org/bots/api#markdownv2-style
_MD2_SPECIALS = set(r"_*[]()~`>#+-=|{}.!")


def _assert_specials_escaped(text: str) -> None:
    """
    For every MD2 special char in `text`, the preceding char must be '\\'.

    We allow bold/italic markers produced by the formatter itself ('*' around
    the header, '_' around meal headers), so skip those two contexts. All other
    specials in the user-visible numeric payloads must be escaped.
    """
    # Remove bold header and italic meal lines (produced by formatter itself)
    cleaned = re.sub(r"\*[^*\n]+\*", "", text)  # *Header*
    cleaned = re.sub(r"(?m)^_[^_\n]+_$", "", cleaned)  # italic meal lines
    # Now no bare '*' or '_' should remain unescaped
    for i, ch in enumerate(cleaned):
        if ch in _MD2_SPECIALS:
            if i == 0 or cleaned[i - 1] != "\\":
                raise AssertionError(
                    f"Unescaped MD2 char {ch!r} at index {i}: "
                    f"context={cleaned[max(0, i - 20):i + 5]!r}"
                )


class TestCpapFieldsEscaped:
    def _day(self, **overrides):
        cpap = {
            "duration_minutes": 412,
            "ahi": 3.2,
            "leak_median": 4.1,
            "pressure_min": 10.2,
            "pressure_max": 14.8,
        }
        cpap.update(overrides)
        return {"date": "2026-04-15", "cpap": cpap}

    def test_pressure_range_escaped(self):
        text = format_day(self._day())
        _assert_specials_escaped(text)
        # Dash between pressures must be escaped, decimal point too
        assert r"10\.2\-14\.8" in text

    def test_leak_decimal_escaped(self):
        text = format_day(self._day(leak_median=4.1))
        _assert_specials_escaped(text)
        assert r"4\.1" in text

    def test_ahi_decimal_escaped(self):
        text = format_day(self._day(ahi=3.2))
        _assert_specials_escaped(text)
        assert r"AHI 3\.2" in text

    def test_cpap_null_pressure_does_not_crash(self):
        text = format_day(self._day(pressure_min=None, pressure_max=None))
        _assert_specials_escaped(text)
        assert "Давл" not in text  # absent pressure = no Давл line

    def test_cpap_null_leak_does_not_crash(self):
        text = format_day(self._day(leak_median=None))
        _assert_specials_escaped(text)
        assert "Утечка" not in text


class TestO2RingFieldsEscaped:
    def _day(self, **overrides):
        o2 = {"avg_spo2": 95.0, "min_spo2": 88.0, "spo2_drops_count": 7, "avg_hr": 62.0}
        o2.update(overrides)
        return {"date": "2026-04-15", "o2ring": o2}

    def test_spo2_integer_rendering(self):
        text = format_day(self._day(avg_spo2=95.0, min_spo2=88.0))
        _assert_specials_escaped(text)
        # Percent sign is not an MD2 special; int rendering should have no '.'
        assert "ср 95%" in text and "мин 88%" in text

    def test_o2ring_null_fields(self):
        text = format_day(self._day(avg_spo2=None, min_spo2=None, avg_hr=None))
        _assert_specials_escaped(text)


class TestWeightHrvEscaped:
    def test_weight_decimal_escaped(self):
        text = format_day({"date": "2026-04-15", "weight": {"weight_kg": 87.2}})
        _assert_specials_escaped(text)
        assert r"87\.2" in text

    def test_weight_null_omitted(self):
        text = format_day({"date": "2026-04-15", "weight": {"weight_kg": None}})
        # no crash; no weight line
        assert "кг" not in text

    def test_hrv_integer_rendering(self):
        text = format_day({"date": "2026-04-15", "hrv": {"rmssd": 42.0}})
        _assert_specials_escaped(text)
        assert "HRV 42 мс" in text

    def test_hrv_null_omitted(self):
        text = format_day({"date": "2026-04-15", "hrv": {"rmssd": None}})
        assert "HRV" not in text


class TestActivityMinutesEscaped:
    def test_active_minutes_has_escaped_dot(self):
        text = format_day({
            "date": "2026-04-15",
            "activity": {
                "steps": 8000,
                "active_minutes_lightly": 30,
                "active_minutes_fairly": 10,
                "active_minutes_very": 5,
            },
        })
        _assert_specials_escaped(text)
        assert r"акт\.мин" in text


class TestNoUnescapedDotInFormatterSource:
    """
    Regression guard: the formatter source must not contain a raw '\\.' or '\\-'
    literal outside of _esc() — such manual escapes were the P0.3 bug.
    """

    def test_no_manual_escape_literals(self):
        import pathlib
        src = pathlib.Path(__file__).parent.parent.parent / "src" / "formatter.py"
        text = src.read_text(encoding="utf-8")
        # Remove the _SPECIAL definition line (that's the regex, not a manual escape)
        scrubbed = re.sub(r"_SPECIAL\s*=.*", "", text)
        assert r'"\\-' not in scrubbed and r"'\\-" not in scrubbed, (
            "Found manual '\\-' literal outside _esc(); use _esc() instead"
        )
        assert r'"\\.' not in scrubbed and r"'\\." not in scrubbed, (
            "Found manual '\\.' literal outside _esc(); use _esc() instead"
        )
