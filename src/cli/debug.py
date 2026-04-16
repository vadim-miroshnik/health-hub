"""
Debug CLI команды: fetch, show, auth.

Работают без Telegram, вывод в stdout.
Используются для разработки, отладки парсеров и инспекции данных.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

if not os.environ.get("NO_DOTENV"):
    load_dotenv(override=True)


def _db_path() -> Path:
    return Path(os.environ.get("DB_PATH", "data/health.db"))


def _raw_dir() -> Path:
    return Path(os.environ.get("RAW_DATA_DIR", "data/raw"))


# ---------------------------------------------------------------------------
# hhub fetch <source> <date>
# ---------------------------------------------------------------------------

def cmd_fetch(args) -> None:
    """
    Забирает сырые данные из источника за дату, сохраняет в raw_store.
    Парсинга в БД нет — только fetch + raw файлы на диск.
    """
    source: str = args.source
    date: str = args.date

    if source == "fitbit":
        _fetch_fitbit(date)
    else:
        print(f"Source '{source}' not yet supported for fetch", file=sys.stderr)
        sys.exit(1)


def _fetch_fitbit(date: str) -> None:
    import requests

    from src.collector import Collector
    from src.db import Database
    from src.fitbit_client import FitbitClient
    from src.raw_store import RawStore

    try:
        client = FitbitClient.from_env()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = _raw_dir()
    raw_dir.mkdir(parents=True, exist_ok=True)

    db = Database(db_path)
    store = RawStore(db.conn, raw_dir)

    endpoints = [
        ("nutrition",    f"/1/user/-/foods/log/date/{date}.json"),
        ("water",        f"/1/user/-/foods/log/water/date/{date}.json"),
        ("activity",     f"/1/user/-/activities/date/{date}.json"),
        ("sleep",        f"/1.2/user/-/sleep/date/{date}.json"),
        ("weight",       f"/1/user/-/body/log/weight/date/{date}.json"),
        ("hrv",          f"/1.2/user/-/hrv/date/{date}.json"),
        ("heart_rate",   f"/1/user/-/activities/heart/date/{date}/1d/1min.json"),
        ("azm",          f"/1/user/-/activities/active-zone-minutes/date/{date}.json"),
        ("br",           f"/1/user/-/br/date/{date}.json"),
        ("spo2",         f"/1/user/-/spo2/date/{date}.json"),
        ("skin_temp",    f"/1/user/-/temp/skin/date/{date}.json"),
        ("cardio_score", f"/1/user/-/cardioscore/date/{date}.json"),
    ]

    print(f"Fetching Fitbit data for {date}:")
    errors = []
    for kind, url in endpoints:
        try:
            raw = client.get(url)
            path = store.save_raw("fitbit", date, kind, json.dumps(raw))
            print(f"  {kind:<12} {path}  ({path.stat().st_size} bytes)")
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (403, 404):
                print(f"  {kind:<12} no data ({exc.response.status_code})")
            else:
                print(f"  {kind:<12} ERROR: {exc}", file=sys.stderr)
                errors.append(kind)
        except Exception as exc:
            print(f"  {kind:<12} ERROR: {exc}", file=sys.stderr)
            errors.append(kind)

    # Devices (no date parameter)
    try:
        raw_devices = client.get("/1/user/-/devices.json")
        path = store.save_raw("fitbit", date, "devices", json.dumps(raw_devices))
        print(f"  {'devices':<12} {path}  ({path.stat().st_size} bytes)")
        db.save_devices(raw_devices)
    except Exception as exc:
        print(f"  {'devices':<12} ERROR: {exc}", file=sys.stderr)
        # don't add to errors — devices is best-effort

    if errors:
        db.close()
        sys.exit(1)

    # Parse fetched raw files into the DB
    print(f"\nParsing into DB:")
    collector = Collector(client, db, store)
    result = collector.reparse_day(date)
    if result.ok:
        print(f"  OK")
    else:
        print(f"  partial — errors: {result.errors}")

    db.close()


# ---------------------------------------------------------------------------
# hhub show <date> [--source SRC]
# ---------------------------------------------------------------------------

def cmd_show(args) -> None:
    """
    Выводит всё что есть в БД за день в JSON.
    --source ограничивает вывод одним источником.
    """
    date: str = args.date
    source: str | None = getattr(args, "source", None)

    db_path = _db_path()
    if not db_path.exists():
        # БД ещё не создана — выводим пустую структуру, не ошибку
        if source is None:
            data: dict = {
                "date": date,
                "nutrition": None, "activity": None, "sleep": [],
                "weight": None, "hrv": None, "cpap": None, "o2ring": None,
            }
        else:
            data = _empty_source_data(date, source)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    from src.db import Database

    with Database(db_path) as db:
        if source is None:
            data = db.get_day(date)
        else:
            data = _get_source_data(db, date, source)

    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _empty_source_data(date: str, source: str) -> dict:
    """Пустая структура для source когда БД ещё не создана."""
    if source == "fitbit":
        return {
            "date": date,
            "nutrition": None, "activity": None, "sleep": [],
            "weight": None, "hrv": None, "food_log": [],
        }
    if source == "cpap":
        return {"date": date, "cpap_session": None, "cpap_events": []}
    if source == "o2ring":
        return {"date": date, "o2ring_session": None}
    return {"date": date}


def _get_source_data(db, date: str, source: str) -> dict:
    if source == "fitbit":
        return {
            "date": date,
            "nutrition": db.get_nutrition(date),
            "activity": db.get_activity(date),
            "sleep": db.get_sleep_sessions(date),
            "weight": db.get_weight(date),
            "hrv": db.get_hrv(date),
            "food_log": db.get_food_log(date),
        }
    if source == "cpap":
        return {
            "date": date,
            "cpap_session": db.get_cpap_session(date),
            "cpap_events": db.get_cpap_events(date),
        }
    if source == "o2ring":
        session = db.get_o2ring_session(date)
        return {
            "date": date,
            "o2ring_session": session,
        }
    print(f"Unknown source: {source!r}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# hhub preview [DATE]
# ---------------------------------------------------------------------------

def cmd_preview(args) -> None:
    """
    Форматирует и выводит текст Telegram-отчёта в stdout без отправки.
    """
    from datetime import date

    date_str = getattr(args, "date", None) or str(date.today() - __import__('datetime').timedelta(days=1))
    db_path = _db_path()

    if not db_path.exists():
        print(f"No database at {db_path}", file=sys.stderr)
        sys.exit(1)

    from src.db import Database
    from src.formatter import format_day

    with Database(db_path) as db:
        data = db.get_day(date_str)

    text = format_day(data)
    if text:
        print(text)
    else:
        print(f"No data for {date_str}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# hhub cpap-parse <date>
# ---------------------------------------------------------------------------

def cmd_cpap_parse(args) -> None:
    """Parse a CPAP EDF file or directory for a date and load into DB."""
    from src.cpap_parser import CpapParser
    from src.db import Database
    from src.raw_store import RawStore

    date: str = args.date
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = _raw_dir()

    db = Database(db_path)
    store = RawStore(db.conn, raw_dir)
    parser = CpapParser(db, store)

    result = parser.parse_date(date)
    db.close()

    if result.status == "no_data":
        print(f"No CPAP EDF files found for {date}")
        print(f"Copy EDF files to: {raw_dir}/cpap/{date}/")
    elif result.status == "ok":
        print(f"Parsed CPAP data for {date}: OK")
    else:
        print(f"Parsed CPAP data for {date}: errors — {result.errors}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# hhub o2ring-parse <date> <file>
# ---------------------------------------------------------------------------

def cmd_o2ring_parse(args) -> None:
    """Parse an O2Ring CSV or binary file for a date and load into DB."""
    from pathlib import Path
    from src.o2ring_collector import O2RingCollector
    from src.db import Database
    from src.raw_store import RawStore

    date: str = args.date
    file_path = Path(args.file)
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = _raw_dir()

    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    db = Database(db_path)
    store = RawStore(db.conn, raw_dir)
    collector = O2RingCollector(db, store)

    if file_path.suffix.lower() in (".bin", ".o2"):
        result = collector.import_binary(date, file_path)
    else:
        result = collector.import_csv(date, file_path)

    db.close()

    if result.status == "ok":
        print(f"Imported O2Ring data for {date}: {result.n_records} records")
    else:
        print(f"Import failed: {result.errors}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# hhub auth check
# ---------------------------------------------------------------------------

def cmd_auth_check(args) -> None:
    """
    Проверяет валидность Fitbit OAuth токена.
    Делает реальный запрос к /1/user/-/profile.json.
    При 401 пробует refresh автоматически.
    """
    from src.fitbit_client import FitbitClient

    try:
        client = FitbitClient.from_env()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        profile = client.get("/1/user/-/profile.json")
    except SystemExit:
        # FitbitClient вызывает exit(1) при невосстановимой ошибке авторизации
        raise
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    user = profile.get("user", {})
    name = user.get("displayName", "unknown")
    member_since = user.get("memberSince", "")
    print(f"Token valid. User: {name}  member since: {member_since}")
