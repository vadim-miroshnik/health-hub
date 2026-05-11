"""
Production CLI команды: daily, backfill, report, status.

Используются в cron и для ручного запуска.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

if not os.environ.get("NO_DOTENV"):
    load_dotenv(override=True)


# ---------------------------------------------------------------------------
# Source configuration detection
# ---------------------------------------------------------------------------

def _source_config() -> dict:
    """
    Определяет статус каждого источника данных на основе env-переменных.

    Возвращает dict вида:
        {
          "fitbit":  {"enabled": True,  "reason": None},
          "cpap":    {"enabled": False, "reason": "CPAP_DATA_DIR not set"},
          "o2ring":  {"enabled": False, "reason": "directory not found: /path/to/..."},
        }
    """
    result = {}

    # --- Fitbit ---
    client_id = os.environ.get("FITBIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("FITBIT_CLIENT_SECRET", "").strip()
    tokens_path = Path(os.environ.get("TOKENS_PATH", "tokens.json"))

    if not client_id or not client_secret:
        result["fitbit"] = {
            "enabled": False,
            "reason": "FITBIT_CLIENT_ID / FITBIT_CLIENT_SECRET not set",
        }
    elif not tokens_path.exists():
        result["fitbit"] = {
            "enabled": False,
            "reason": f"tokens file not found: {tokens_path} (run: make auth)",
        }
    else:
        result["fitbit"] = {"enabled": True, "reason": None}

    # --- CPAP ---
    cpap_dir_str = os.environ.get("CPAP_DATA_DIR", "").strip()
    if not cpap_dir_str:
        result["cpap"] = {"enabled": False, "reason": "CPAP_DATA_DIR not set"}
    else:
        cpap_dir = Path(cpap_dir_str)
        if not cpap_dir.exists():
            result["cpap"] = {
                "enabled": False,
                "reason": f"directory not found: {cpap_dir}",
            }
        else:
            result["cpap"] = {"enabled": True, "reason": None}

    # --- O2Ring ---
    o2ring_dir_str = os.environ.get("O2RING_DATA_DIR", "").strip()
    if not o2ring_dir_str:
        result["o2ring"] = {"enabled": False, "reason": "O2RING_DATA_DIR not set"}
    else:
        o2ring_dir = Path(o2ring_dir_str)
        if not o2ring_dir.exists():
            result["o2ring"] = {
                "enabled": False,
                "reason": f"directory not found: {o2ring_dir}",
            }
        else:
            result["o2ring"] = {"enabled": True, "reason": None}

    return result


def _db_coverage(db_path: Path) -> dict:
    """
    Читает из БД диапазоны дат и последнюю синхронизацию.
    Возвращает пустой dict если БД не существует.
    """
    if not db_path.exists():
        return {}

    from src.db import Database

    with Database(db_path) as db:
        coverage = {}
        queries = {
            "fitbit": (
                "SELECT MIN(date), MAX(date), COUNT(*) FROM daily_activity"
            ),
            "sleep": (
                "SELECT MIN(date_of_sleep), MAX(date_of_sleep), COUNT(DISTINCT date_of_sleep)"
                " FROM sleep_sessions"
            ),
            "cpap": (
                "SELECT MIN(date), MAX(date), COUNT(*) FROM cpap_sessions"
            ),
            "o2ring": (
                "SELECT MIN(date), MAX(date), COUNT(*) FROM o2ring_sessions"
            ),
        }
        for key, sql in queries.items():
            row = db.conn.execute(sql).fetchone()
            if row and row[0]:
                coverage[key] = {
                    "first": row[0],
                    "last": row[1],
                    "days": row[2],
                }

        last_sync = db.conn.execute(
            "SELECT date, synced_at, status FROM sync_log ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if last_sync:
            coverage["last_sync"] = dict(last_sync)

    return coverage


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------

def cmd_status(args) -> None:
    db_path = Path(os.environ.get("DB_PATH", "data/health.db"))
    sources = _source_config()
    coverage = _db_coverage(db_path)

    print("Sources:")
    labels = {"fitbit": "Fitbit", "cpap": "CPAP", "o2ring": "O2Ring"}
    for key, label in labels.items():
        s = sources[key]
        if s["enabled"]:
            status_str = "enabled"
        else:
            status_str = f"disabled ({s['reason']})"
        print(f"  {label:<8} {status_str}")

    print()
    if not db_path.exists():
        print(f"Database: not found ({db_path})")
        return

    print(f"Database: {db_path}")

    cov_keys = [
        ("fitbit",  "Fitbit activity"),
        ("sleep",   "Fitbit sleep"),
        ("cpap",    "CPAP"),
        ("o2ring",  "O2Ring"),
    ]
    has_data = False
    for key, label in cov_keys:
        if key in coverage:
            c = coverage[key]
            print(f"  {label:<18} {c['first']} → {c['last']}  ({c['days']} days)")
            has_data = True

    if not has_data:
        print("  No data yet")

    if "last_sync" in coverage:
        ls = coverage["last_sync"]
        print(f"\nLast sync: {ls['date']}  status={ls['status']}  at {ls['synced_at'][:19]}")


# ---------------------------------------------------------------------------
# Stubs для будущих фаз
# ---------------------------------------------------------------------------

def cmd_daily(args) -> None:
    from datetime import date
    from pathlib import Path

    from src.db import Database
    from src.formatter import format_day
    from src.telegram import TelegramClient

    db_path = Path(os.environ.get("DB_PATH", "data/health.db"))
    raw_dir = Path(os.environ.get("RAW_DATA_DIR", "data/raw"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    today = str(date.today())
    sources = _source_config()

    db = Database(db_path)

    if sources["fitbit"]["enabled"]:
        from src.collector import Collector
        from src.fitbit_client import AuthError, FitbitClient
        from src.raw_store import RawStore

        try:
            client = FitbitClient.from_env()
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            db.close()
            sys.exit(1)

        store = RawStore(db.conn, raw_dir)
        collector = Collector(client, db, store)
        print(f"Collecting Fitbit data for {today}...")
        try:
            result = collector.collect_day(today, force=False)
        except AuthError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            db.close()
            sys.exit(1)
        if result.errors:
            print(f"  partial — {result.errors}", file=sys.stderr)
    else:
        print(f"Fitbit collector skipped: {sources['fitbit']['reason']}")

    data = db.get_day(today)
    db.close()

    text = format_day(data)
    if not text:
        print(f"No data to report for {today}", file=sys.stderr)
        sys.exit(1)

    tg = TelegramClient.from_env()
    if tg.enabled:
        tg.send(text)
        print(f"Report sent for {today}")
    else:
        print(text)
        print("\n[Telegram not configured — printed to stdout]", file=sys.stderr)


def cmd_backfill(args) -> None:
    from datetime import date
    from pathlib import Path
    import os

    from src.backfill import Backfill
    from src.collector import Collector
    from src.db import Database
    from src.fitbit_client import FitbitClient
    from src.raw_store import RawStore

    db_path = Path(os.environ.get("DB_PATH", "data/health.db"))
    raw_dir = Path(os.environ.get("RAW_DATA_DIR", "data/raw"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    sources = _source_config()
    if not sources["fitbit"]["enabled"]:
        print(
            f"ERROR: backfill requires Fitbit credentials — {sources['fitbit']['reason']}",
            file=sys.stderr,
        )
        sys.exit(1)

    from src.fitbit_client import AuthError

    try:
        client = FitbitClient.from_env()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    db = Database(db_path)
    store = RawStore(db.conn, raw_dir)
    collector = Collector(client, db, store)
    backfill = Backfill(collector, client, db)

    start = date.fromisoformat(args.start) if args.start else None
    end   = date.fromisoformat(args.end)   if args.end   else None

    print(f"Starting backfill"
          + (f" from {start}" if start else " from memberSince")
          + (f" to {end}" if end else " to yesterday"))

    try:
        result = backfill.run(
            start=start,
            end=end,
            source=args.source,
            force=getattr(args, "force", False),
        )
    except AuthError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        db.close()
        sys.exit(1)

    db.close()

    print(f"\nDone: {result.synced} synced, {result.skipped} skipped, {result.errors} errors")
    if result.failed_dates:
        print(f"Failed dates: {', '.join(result.failed_dates[:10])}"
              + (" ..." if len(result.failed_dates) > 10 else ""))
    if result.errors:
        sys.exit(1)


def cmd_report(args) -> None:
    import os
    from datetime import date

    from src.db import Database
    from src.formatter import format_day
    from src.telegram import TelegramClient

    db_path = Path(os.environ.get("DB_PATH", "data/health.db"))
    date_str = args.date or str(date.today() - __import__('datetime').timedelta(days=1))

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    with Database(db_path) as db:
        data = db.get_day(date_str)

    text = format_day(data)
    if not text:
        print(f"No data for {date_str}", file=sys.stderr)
        sys.exit(1)

    tg = TelegramClient.from_env()
    if tg.enabled:
        tg.send(text)
        print(f"Sent report for {date_str}")
    else:
        print(text)
        print("\n[Telegram not configured — printed to stdout]", file=sys.stderr)
