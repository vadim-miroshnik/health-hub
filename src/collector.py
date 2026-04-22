"""
Оркестрация сбора данных Fitbit за один день.

Поток для каждого эндпоинта:
    fetch → raw_store.save_raw → parse → db.save_*

Ошибка одного эндпоинта не останавливает остальные — записывается
partial в sync_log, raw-файлы уже на диске для повторного парсинга.

Использование:
    from src.collector import Collector
    collector = Collector(client, db, store)
    result = collector.collect_day("2026-04-15")
    # result.status  → 'ok' | 'partial' | 'error' | 'skipped'
    # result.errors  → список строк с описанием ошибок
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import requests

from src.db import Database
from src.fitbit_client import AuthError, FitbitClient
from src.raw_store import RawStore

# mealTypeId → название приёма пищи
_MEAL_TYPE: dict[int, str] = {
    1: "Breakfast",
    2: "Morning Snack",
    3: "Lunch",
    4: "Afternoon Snack",
    5: "Dinner",
    7: "Anytime",
}

_ENDPOINTS: list[tuple[str, str]] = [
    ("nutrition",    "/1/user/-/foods/log/date/{date}.json"),
    ("water",        "/1/user/-/foods/log/water/date/{date}.json"),
    ("activity",     "/1/user/-/activities/date/{date}.json"),
    ("sleep",        "/1.2/user/-/sleep/date/{date}.json"),
    ("weight",       "/1/user/-/body/log/weight/date/{date}.json"),
    ("hrv",          "/1.2/user/-/hrv/date/{date}.json"),
    ("heart_rate",   "/1/user/-/activities/heart/date/{date}/1d/1min.json"),
    ("azm",          "/1/user/-/activities/active-zone-minutes/date/{date}.json"),
    ("br",           "/1/user/-/br/date/{date}.json"),
    ("spo2",         "/1/user/-/spo2/date/{date}.json"),
    ("skin_temp",    "/1/user/-/temp/skin/date/{date}.json"),
    ("cardio_score", "/1/user/-/cardioscore/date/{date}.json"),
]


@dataclass
class CollectResult:
    status: str                    # 'ok' | 'partial' | 'error' | 'skipped'
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "skipped")


# ===========================================================================
# Parsers (module-level, для переиспользования в `hhub parse`)
# ===========================================================================

def parse_nutrition(date: str, data: dict, db: Database) -> None:
    """Парсит /foods/log/date/{date}.json → daily_nutrition + food_log."""
    summary = data.get("summary", {})
    db.save_nutrition(
        date,
        calories=_int_or_none(summary.get("calories")),
        protein_g=_float_or_none(summary.get("protein")),
        fat_g=_float_or_none(summary.get("fat")),
        carbs_g=_float_or_none(summary.get("carbs")),
        fiber_g=_float_or_none(summary.get("fiber")),
    )
    entries = [_parse_food_item(f) for f in data.get("foods", [])]
    db.save_food_log(date, entries)


def parse_water(date: str, data: dict, db: Database) -> None:
    """Парсит /foods/log/water/date/{date}.json → daily_nutrition.water_ml."""
    water = data.get("summary", {}).get("water")
    if water is not None:
        db.save_water(date, float(water))


def parse_activity(date: str, data: dict, db: Database) -> None:
    """Парсит /activities/date/{date}.json → daily_activity + activity_log."""
    summary = data.get("summary", {})
    distances = {
        d["activity"]: d["distance"]
        for d in summary.get("distances", [])
    }
    db.save_activity(
        date,
        steps=_int_or_none(summary.get("steps")),
        distance_km=_float_or_none(distances.get("total")),
        floors=_int_or_none(summary.get("floors")),
        calories_burned=_int_or_none(summary.get("caloriesOut")),
        active_minutes_lightly=_int_or_none(summary.get("lightlyActiveMinutes")),
        active_minutes_fairly=_int_or_none(summary.get("fairlyActiveMinutes")),
        active_minutes_very=_int_or_none(summary.get("veryActiveMinutes")),
        sedentary_minutes=_int_or_none(summary.get("sedentaryMinutes")),
    )
    # Extract individual activity logs (workouts)
    activities = data.get("activities", [])
    if activities:
        db.save_activity_log(date, activities)


def parse_sleep(date: str, data: dict, db: Database) -> None:
    """
    Парсит /1.2/sleep/date/{date}.json → sleep_sessions + sleep_stages.

    Обрабатывает типы 'stages' и 'classic'.
    Пустой массив sleep → no-op.
    """
    for session in data.get("sleep", []):
        _save_sleep_session(db, session)


def parse_weight(date: str, data: dict, db: Database) -> None:
    """Парсит /body/log/weight/date/{date}.json → daily_weight."""
    entries = data.get("weight", [])
    if not entries:
        return
    w = entries[0]  # берём первую запись за день
    db.save_weight(
        date,
        weight_kg=_float_or_none(w.get("weight")),
        bmi=_float_or_none(w.get("bmi")),
        fat_percent=_float_or_none(w.get("fat")),
    )


def parse_hrv(date: str, data: dict, db: Database) -> None:
    """Парсит /1.2/hrv/date/{date}.json → daily_hrv."""
    entries = data.get("hrv", [])
    if not entries:
        return
    value = entries[0].get("value", {})
    db.save_hrv(
        date,
        rmssd=_float_or_none(value.get("dailyRmssd")),
        coverage=_float_or_none(value.get("coverage")),
        low_freq=_float_or_none(value.get("lowFrequency")),
        high_freq=_float_or_none(value.get("highFrequency")),
    )


def parse_heart_rate(date: str, data: dict, db: Database) -> None:
    """
    Парсит /activities/heart/date/{date}/1d/1min.json
    → daily_heart_rate + hr_intraday.
    """
    summary_list = data.get("activities-heart", [])
    if not summary_list:
        return
    summary = summary_list[0].get("value", {})
    resting_hr = summary.get("restingHeartRate")
    zones = summary.get("heartRateZones", [])
    db.save_heart_rate(date, resting_hr=resting_hr, zones=zones)

    intraday = data.get("activities-heart-intraday", {})
    entries = intraday.get("dataset", [])
    if entries:
        db.save_hr_intraday(date, entries)


def parse_azm(date: str, data: dict, db: Database) -> None:
    """Парсит /activities/active-zone-minutes/date/{date}.json → daily_azm."""
    summary_list = data.get("activities-active-zone-minutes", [])
    if not summary_list:
        return
    value = summary_list[0].get("value", {})
    db.save_azm(
        date,
        fat_burn=value.get("fatBurnActiveZoneMinutes"),
        cardio=value.get("cardioActiveZoneMinutes"),
        peak=value.get("peakActiveZoneMinutes"),
    )


def parse_br(date: str, data: dict, db: Database) -> None:
    """
    Парсит /br/date/{date}.json → daily_health_metrics.breathing_rate.
    Может вернуть 404 на неподдерживаемых устройствах.
    """
    br_list = data.get("br", [])
    if not br_list:
        return
    value = br_list[0].get("value", {})
    breathing_rate = value.get("breathingRate")
    if breathing_rate is not None:
        db.save_health_metrics(date, breathing_rate=breathing_rate)


def parse_spo2(date: str, data: dict, db: Database) -> None:
    """Парсит /spo2/date/{date}.json → daily_health_metrics.spo2_*."""
    value = data.get("value", {})
    spo2_avg = value.get("avg")
    spo2_min = value.get("min")
    if spo2_avg is not None or spo2_min is not None:
        db.save_health_metrics(date, spo2_avg=spo2_avg, spo2_min=spo2_min)


def parse_skin_temp(date: str, data: dict, db: Database) -> None:
    """Парсит /temp/skin/date/{date}.json → daily_health_metrics.skin_temp_delta."""
    temp_list = data.get("tempSkin", [])
    if not temp_list:
        return
    value = temp_list[0].get("value", {})
    delta = value.get("nightlyRelative")
    if delta is not None:
        db.save_health_metrics(date, skin_temp_delta=delta)


def parse_cardio_score(date: str, data: dict, db: Database) -> None:
    """Парсит /cardioscore/date/{date}.json → daily_health_metrics.cardio_score_*."""
    score_list = data.get("cardioScore", [])
    if not score_list:
        return
    vo2_str = score_list[0].get("value", {}).get("vo2Max", "")
    if not vo2_str:
        return
    # Parse "42-46" range
    parts = str(vo2_str).split("-")
    try:
        score_min = float(parts[0])
        score_max = float(parts[-1])
    except (ValueError, IndexError):
        return
    db.save_health_metrics(date, cardio_score_min=score_min, cardio_score_max=score_max)


# ---------------------------------------------------------------------------
# Вспомогательные парсеры
# ---------------------------------------------------------------------------

def _save_sleep_session(db: Database, session: dict) -> None:
    """Сохраняет одну sleep session + все её stages."""
    log_id = session["logId"]
    sleep_type = session.get("type", "stages")
    levels = session.get("levels", {})
    summary = levels.get("summary", {})

    if sleep_type == "stages":
        deep_min  = _int_or_none(summary.get("deep",  {}).get("minutes"))
        light_min = _int_or_none(summary.get("light", {}).get("minutes"))
        rem_min   = _int_or_none(summary.get("rem",   {}).get("minutes"))
        wake_min  = _int_or_none(summary.get("wake",  {}).get("minutes"))
        asleep_min = restless_min = awake_min = None
    else:  # classic
        asleep_min   = _int_or_none(summary.get("asleep",   {}).get("minutes"))
        restless_min = _int_or_none(summary.get("restless", {}).get("minutes"))
        awake_min    = _int_or_none(summary.get("awake",    {}).get("minutes"))
        deep_min = light_min = rem_min = wake_min = None

    # duration в API — миллисекунды
    duration_ms = session.get("duration", 0) or 0
    duration_min = duration_ms // 60000

    db.save_sleep_session(
        log_id=log_id,
        date_of_sleep=session["dateOfSleep"],
        start_time=session["startTime"],
        end_time=session["endTime"],
        duration_minutes=duration_min or None,
        efficiency=_int_or_none(session.get("efficiency")),
        is_main_sleep=bool(session.get("isMainSleep", True)),
        log_type=session.get("logType"),
        sleep_type=sleep_type,
        deep_minutes=deep_min,
        light_minutes=light_min,
        rem_minutes=rem_min,
        wake_minutes=wake_min,
        asleep_minutes=asleep_min,
        restless_minutes=restless_min,
        awake_minutes=awake_min,
        minutes_to_fall_asleep=_int_or_none(session.get("minutesToFallAsleep")),
        minutes_after_wakeup=_int_or_none(session.get("minutesAfterWakeup")),
        time_in_bed=_int_or_none(session.get("timeInBed")),
    )

    stages: list[dict] = []
    for item in levels.get("data", []):
        stages.append({
            "date_time": item["dateTime"],
            "level": item["level"],
            "seconds": item["seconds"],
            "is_short": False,
        })
    for item in levels.get("shortData", []):
        stages.append({
            "date_time": item["dateTime"],
            "level": item["level"],
            "seconds": item["seconds"],
            "is_short": True,
        })

    if stages:
        db.save_sleep_stages(log_id, stages)


def _parse_food_item(item: dict) -> dict:
    logged = item.get("loggedFood", {})
    nutr = item.get("nutritionalValues", {})
    unit_obj = logged.get("unit")
    return {
        "meal_type": _MEAL_TYPE.get(item.get("mealTypeId"), "Anytime"),
        "food_name": logged.get("name"),
        "calories": _int_or_none(nutr.get("calories")),
        "protein_g": _float_or_none(nutr.get("protein")),
        "fat_g": _float_or_none(nutr.get("fat")),
        "carbs_g": _float_or_none(nutr.get("carbs")),
        "amount": _float_or_none(logged.get("amount")),
        "unit": unit_obj.get("name") if isinstance(unit_obj, dict) else None,
    }


def _int_or_none(v) -> int | None:
    return int(v) if v is not None else None


def _float_or_none(v) -> float | None:
    return float(v) if v is not None else None


# ===========================================================================
# Collector
# ===========================================================================

_PARSERS = {
    "nutrition":    parse_nutrition,
    "water":        parse_water,
    "activity":     parse_activity,
    "sleep":        parse_sleep,
    "weight":       parse_weight,
    "hrv":          parse_hrv,
    "heart_rate":   parse_heart_rate,
    "azm":          parse_azm,
    "br":           parse_br,
    "spo2":         parse_spo2,
    "skin_temp":    parse_skin_temp,
    "cardio_score": parse_cardio_score,
}


class Collector:
    """
    Оркестрирует сбор всех эндпоинтов Fitbit за один день.

    Params:
        client — FitbitClient (авторизованный)
        db     — Database
        store  — RawStore
    """

    def __init__(
        self,
        client: FitbitClient,
        db: Database,
        store: RawStore,
    ) -> None:
        self.client = client
        self.db = db
        self.store = store

    def collect_day(self, date: str, force: bool = False) -> CollectResult:
        """
        Собирает все эндпоинты за указанную дату.

        force=False — пропускает день если уже есть status='ok' в sync_log.
        Возвращает CollectResult с итоговым статусом и списком ошибок.
        """
        if not force and self.db.is_date_synced("fitbit", date):
            return CollectResult(status="skipped")

        errors: list[str] = []

        for kind, url_template in _ENDPOINTS:
            url = url_template.format(date=date)
            try:
                raw = self.client.get(url)
                self.store.save_raw("fitbit", date, kind, json.dumps(raw))
                _PARSERS[kind](date, raw, self.db)
            except AuthError:
                raise  # auth failures are fatal — don't swallow
            except requests.exceptions.HTTPError as exc:
                # 404 = no data for this date / unsupported metric — silent.
                # 403 = permission or scope issue — surface it.
                if exc.response is not None and exc.response.status_code == 404:
                    pass
                else:
                    errors.append(f"{kind}: {exc}")
            except Exception as exc:
                errors.append(f"{kind}: {exc}")

        # Devices endpoint is date-independent. We don't raw-archive it — the
        # snapshot is small and the latest row lives in the `devices` table
        # (`devices.updated_at` is the sole timestamp).
        try:
            devices_raw = self.client.get("/1/user/-/devices.json")
            if isinstance(devices_raw, list):
                self.db.save_devices(devices_raw)
        except AuthError:
            raise
        except requests.exceptions.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                errors.append(f"devices: {exc}")
        except Exception as exc:
            errors.append(f"devices: {exc}")

        if not errors:
            status = "ok"
        elif len(errors) < len(_ENDPOINTS):
            status = "partial"
        else:
            status = "error"

        self.db.upsert_sync_log(date, status, errors or None)
        return CollectResult(status=status, errors=errors)

    def reparse_day(self, date: str) -> CollectResult:
        """
        Повторно парсит уже сохранённые raw-файлы без обращения к API.
        Полезно для отладки парсеров: hhub parse fitbit <date>.
        """
        errors: list[str] = []

        for kind, _ in _ENDPOINTS:
            try:
                path = self.store.get_raw("fitbit", date, kind)
                raw = json.loads(path.read_text(encoding="utf-8"))
                _PARSERS[kind](date, raw, self.db)
            except FileNotFoundError:
                pass  # не все эндпоинты могут быть на диске
            except Exception as exc:
                errors.append(f"{kind}: {exc}")

        status = "ok" if not errors else "partial"
        self.db.upsert_sync_log(date, status, errors or None)
        return CollectResult(status=status, errors=errors)
