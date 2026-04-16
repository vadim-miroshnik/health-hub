"""
Юнит-тесты для src/backfill.py.
"""

from datetime import date

import pytest
from freezegun import freeze_time

from src.backfill import Backfill, BackfillResult
from src.collector import CollectResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backfill(mocker, db, *, is_date_synced=False, collect_status="ok"):
    """Create a Backfill with mocked collector and client."""
    client = mocker.MagicMock()
    collector = mocker.MagicMock()

    mocker.patch.object(db, "is_date_synced", return_value=is_date_synced)
    collector.collect_day.return_value = CollectResult(
        status=collect_status, errors=[] if collect_status != "error" else ["some: 500"]
    )

    return Backfill(collector, client, db)


# ---------------------------------------------------------------------------
# get_member_since
# ---------------------------------------------------------------------------

class TestGetMemberSince:
    def test_get_member_since(self, mocker, db):
        client = mocker.MagicMock()
        client.get.return_value = {"user": {"memberSince": "2025-06-01"}}
        collector = mocker.MagicMock()
        bf = Backfill(collector, client, db)

        result = bf.get_member_since()

        assert result == date(2025, 6, 1)
        client.get.assert_called_once_with("/1/user/-/profile.json")


# ---------------------------------------------------------------------------
# run() — skipping / syncing
# ---------------------------------------------------------------------------

class TestRunSkipsSyncedDays:
    def test_run_skips_synced_days(self, mocker, db):
        bf = _make_backfill(mocker, db, is_date_synced=True)

        start = date(2026, 4, 10)
        end = date(2026, 4, 12)  # 3 days
        result = bf.run(start=start, end=end, progress=False)

        assert result.skipped == 3
        assert result.synced == 0
        assert result.total == 3
        bf.collector.collect_day.assert_not_called()


class TestRunSyncsUnsyncedDays:
    def test_run_syncs_unsynced_days(self, mocker, db):
        bf = _make_backfill(mocker, db, is_date_synced=False, collect_status="ok")

        start = date(2026, 4, 10)
        end = date(2026, 4, 12)  # 3 days
        result = bf.run(start=start, end=end, progress=False)

        assert result.synced == 3
        assert result.skipped == 0
        assert result.errors == 0
        assert result.total == 3
        assert bf.collector.collect_day.call_count == 3


class TestRunCountsErrors:
    def test_run_counts_errors(self, mocker, db):
        bf = _make_backfill(mocker, db, is_date_synced=False, collect_status="error")

        start = date(2026, 4, 10)
        end = date(2026, 4, 12)  # 3 days
        result = bf.run(start=start, end=end, progress=False)

        assert result.errors == 3
        assert result.synced == 0
        assert len(result.failed_dates) == 3
        assert "2026-04-10" in result.failed_dates
        assert "2026-04-11" in result.failed_dates
        assert "2026-04-12" in result.failed_dates


# ---------------------------------------------------------------------------
# run() — start/end defaults
# ---------------------------------------------------------------------------

class TestRunUsesMemberSinceWhenStartNone:
    def test_run_uses_member_since_when_start_none(self, mocker, db):
        client = mocker.MagicMock()
        client.get.return_value = {"user": {"memberSince": "2026-04-10"}}
        collector = mocker.MagicMock()
        mocker.patch.object(db, "is_date_synced", return_value=False)
        collector.collect_day.return_value = CollectResult(status="ok", errors=[])

        bf = Backfill(collector, client, db)

        # end is explicit so we control the range
        result = bf.run(end=date(2026, 4, 12), progress=False)

        assert result.total == 3  # 10, 11, 12
        client.get.assert_called_once_with("/1/user/-/profile.json")

        # First call should be for 2026-04-10
        first_call_date = collector.collect_day.call_args_list[0][0][0]
        assert first_call_date == "2026-04-10"


class TestRunUsesYesterdayWhenEndNone:
    @freeze_time("2026-04-16")
    def test_run_uses_yesterday_when_end_none(self, mocker, db):
        client = mocker.MagicMock()
        collector = mocker.MagicMock()
        mocker.patch.object(db, "is_date_synced", return_value=False)
        collector.collect_day.return_value = CollectResult(status="ok", errors=[])

        bf = Backfill(collector, client, db)

        result = bf.run(start=date(2026, 4, 14), progress=False)

        # 2026-04-16 is today, yesterday = 2026-04-15 → dates: 14, 15
        assert result.total == 2
        last_call_date = collector.collect_day.call_args_list[-1][0][0]
        assert last_call_date == "2026-04-15"


# ---------------------------------------------------------------------------
# run() — partial status counts as error
# ---------------------------------------------------------------------------

class TestRunPartialCountsAsError:
    def test_run_partial_counts_as_error(self, mocker, db):
        client = mocker.MagicMock()
        collector = mocker.MagicMock()
        mocker.patch.object(db, "is_date_synced", return_value=False)
        collector.collect_day.return_value = CollectResult(
            status="partial", errors=["nutrition: 500"]
        )

        bf = Backfill(collector, client, db)

        result = bf.run(
            start=date(2026, 4, 10),
            end=date(2026, 4, 11),
            progress=False,
        )

        assert result.errors == 2
        assert result.synced == 0
        assert "2026-04-10" in result.failed_dates
        assert "2026-04-11" in result.failed_dates


# ---------------------------------------------------------------------------
# run() — force flag
# ---------------------------------------------------------------------------

class TestRunForceDoesNotSkip:
    def test_run_force_does_not_skip(self, mocker, db):
        client = mocker.MagicMock()
        collector = mocker.MagicMock()
        # is_date_synced returns True, but force=True means we should still call collect_day
        mocker.patch.object(db, "is_date_synced", return_value=True)
        collector.collect_day.return_value = CollectResult(status="ok", errors=[])

        bf = Backfill(collector, client, db)

        result = bf.run(
            start=date(2026, 4, 10),
            end=date(2026, 4, 11),
            force=True,
            progress=False,
        )

        assert result.skipped == 0
        assert result.synced == 2
        assert collector.collect_day.call_count == 2
        # Verify force=True is passed through to collect_day
        collector.collect_day.assert_any_call("2026-04-10", force=True)


# ---------------------------------------------------------------------------
# run() — telegram progress notification
# ---------------------------------------------------------------------------

class TestRunTelegramProgress:
    def test_telegram_called_every_100_days(self, mocker, db):
        client = mocker.MagicMock()
        collector = mocker.MagicMock()
        mocker.patch.object(db, "is_date_synced", return_value=False)
        collector.collect_day.return_value = CollectResult(status="ok", errors=[])

        telegram_mock = mocker.MagicMock()
        bf = Backfill(collector, client, db, telegram=telegram_mock)

        # 100 days: 2026-01-01 to 2026-04-10 inclusive = 100 days
        start = date(2026, 1, 1)
        end = date(2026, 4, 10)
        result = bf.run(start=start, end=end, progress=False)

        assert result.total == 100
        telegram_mock.assert_called_once()
        msg = telegram_mock.call_args[0][0]
        assert "Backfill progress" in msg
        assert "100" in msg
