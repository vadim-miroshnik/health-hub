"""
P2.3: formatter surfaces a "Sync issues" footer when the day's sync_log
status is not 'ok', so Telegram readers see that a run was partial without
having to SSH into the server.
"""

from src.formatter import format_day


def _base_day(sync_status: dict | None) -> dict:
    return {
        "date": "2026-04-15",
        "activity": {"steps": 4000},
        "sync_status": sync_status,
    }


def test_no_footer_when_ok():
    text = format_day(_base_day({
        "status": "ok",
        "synced_at": "2026-04-15T21:00:00Z",
        "errors": None,
    }))
    assert "Sync" not in text
    assert "⚠️" not in text


def test_no_footer_when_status_missing():
    text = format_day(_base_day(None))
    assert "Sync" not in text


def test_partial_status_with_errors_shows_count():
    text = format_day(_base_day({
        "status": "partial",
        "errors": ["hrv: 500", "sleep: timeout"],
    }))
    assert r"Sync issues \(2\)" in text
    assert "⚠️" in text


def test_partial_status_no_errors_list_shows_status_label():
    text = format_day(_base_day({
        "status": "partial",
        "errors": None,
    }))
    assert "Sync status" in text
    assert "⚠️" in text


def test_error_status_shows_footer():
    text = format_day(_base_day({
        "status": "error",
        "errors": ["fitbit: 500"],
    }))
    assert r"Sync issues \(1\)" in text
