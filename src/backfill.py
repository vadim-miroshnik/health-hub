"""
Исторический backfill данных Fitbit.

Загружает все данные от memberSince до вчера.
Пропускает уже синхронизированные дни (is_date_synced).
Возобновляем: при обрыве перезапуск продолжает с первого несинхронизированного дня.
"""

import logging
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

logger = logging.getLogger(__name__)


@dataclass
class BackfillResult:
    total: int = 0
    synced: int = 0
    skipped: int = 0      # already synced
    errors: int = 0
    failed_dates: list = field(default_factory=list)


class Backfill:
    def __init__(self, collector, client, db, *, telegram=None):
        self.collector = collector
        self.client = client
        self.db = db
        self.telegram = telegram   # optional callable(message: str)

    def get_member_since(self) -> date:
        """GET /1/user/-/profile.json → memberSince → date object."""
        profile = self.client.get("/1/user/-/profile.json")
        member_since_str = profile["user"]["memberSince"]  # "YYYY-MM-DD"
        return date.fromisoformat(member_since_str)

    def run(
        self,
        start: date | None = None,
        end: date | None = None,
        source: str | None = None,
        force: bool = False,
        progress: bool = True,
    ) -> BackfillResult:
        """
        Backfill от start до end включительно.
        start по умолчанию = memberSince, end = yesterday.
        source пока игнорируется (только fitbit реализован).
        progress = True → tqdm прогресс-бар в stderr.
        """
        if start is None:
            start = self.get_member_since()
        if end is None:
            end = date.today() - timedelta(days=1)

        # Build list of all dates from start to end inclusive
        dates = []
        current = start
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)

        if progress:
            try:
                from tqdm import tqdm
                dates = tqdm(dates, desc="Backfill", unit="day", file=sys.stderr)
            except ImportError:
                pass

        result = BackfillResult()

        for d in dates:
            result.total += 1

            if not force and self.db.is_date_synced("fitbit", str(d)):
                result.skipped += 1
                continue

            collect_result = self.collector.collect_day(str(d), force=force)

            if collect_result.ok:
                result.synced += 1
            else:
                result.errors += 1
                result.failed_dates.append(str(d))

            if result.total % 100 == 0 and self.telegram is not None and result.total > 0:
                msg = (
                    f"Backfill progress: {result.synced}/{result.total} days synced, "
                    f"{result.errors} errors"
                )
                self.telegram(msg)

        return result
