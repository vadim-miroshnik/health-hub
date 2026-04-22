"""
SQLite storage layer для структурированных данных.

Открывает соединение с WAL + NORMAL sync, применяет миграции,
предоставляет методы записи и чтения для всех таблиц.

Использование:
    from src.db import Database
    db = Database(Path("data/health.db"))
    db.save_nutrition("2026-04-15", calories=1842, protein_g=98, ...)
    row = db.get_nutrition("2026-04-15")
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.migrations import run_migrations

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


class Database:
    """
    Обёртка над sqlite3.Connection с методами для всех таблиц health-hub.

    Params:
        path           — путь к файлу БД (':memory:' для тестов)
        migrations_dir — директория с SQL-миграциями
    """

    def __init__(
        self,
        path: Path | str,
        migrations_dir: Path = _MIGRATIONS_DIR,
        *,
        readonly: bool = False,
    ) -> None:
        if readonly:
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
            return

        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        run_migrations(self.conn, migrations_dir)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ==================================================================
    # Fitbit — запись
    # ==================================================================

    def save_nutrition(
        self,
        date: str,
        calories: int | None = None,
        protein_g: float | None = None,
        fat_g: float | None = None,
        carbs_g: float | None = None,
        fiber_g: float | None = None,
        water_ml: float | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO daily_nutrition(date, calories, protein_g, fat_g, carbs_g, fiber_g, water_ml)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                calories  = excluded.calories,
                protein_g = excluded.protein_g,
                fat_g     = excluded.fat_g,
                carbs_g   = excluded.carbs_g,
                fiber_g   = excluded.fiber_g,
                water_ml  = COALESCE(excluded.water_ml, daily_nutrition.water_ml)
            """,
            (date, calories, protein_g, fat_g, carbs_g, fiber_g, water_ml),
        )
        self.conn.commit()

    def save_water(self, date: str, water_ml: float) -> None:
        """Обновляет только water_ml в daily_nutrition, не трогая остальные поля."""
        self.conn.execute(
            "INSERT OR IGNORE INTO daily_nutrition(date) VALUES (?)", (date,)
        )
        self.conn.execute(
            "UPDATE daily_nutrition SET water_ml=? WHERE date=?", (water_ml, date)
        )
        self.conn.commit()

    def save_activity(
        self,
        date: str,
        steps: int | None = None,
        distance_km: float | None = None,
        floors: int | None = None,
        calories_burned: int | None = None,
        active_minutes_lightly: int | None = None,
        active_minutes_fairly: int | None = None,
        active_minutes_very: int | None = None,
        sedentary_minutes: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO daily_activity(
                date, steps, distance_km, floors, calories_burned,
                active_minutes_lightly, active_minutes_fairly,
                active_minutes_very, sedentary_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                steps                   = excluded.steps,
                distance_km             = excluded.distance_km,
                floors                  = excluded.floors,
                calories_burned         = excluded.calories_burned,
                active_minutes_lightly  = excluded.active_minutes_lightly,
                active_minutes_fairly   = excluded.active_minutes_fairly,
                active_minutes_very     = excluded.active_minutes_very,
                sedentary_minutes       = excluded.sedentary_minutes
            """,
            (
                date, steps, distance_km, floors, calories_burned,
                active_minutes_lightly, active_minutes_fairly,
                active_minutes_very, sedentary_minutes,
            ),
        )
        self.conn.commit()

    def save_sleep_session(
        self,
        log_id: int,
        date_of_sleep: str,
        start_time: str,
        end_time: str,
        duration_minutes: int | None = None,
        efficiency: int | None = None,
        is_main_sleep: bool = True,
        log_type: str | None = None,
        sleep_type: str | None = None,
        deep_minutes: int | None = None,
        light_minutes: int | None = None,
        rem_minutes: int | None = None,
        wake_minutes: int | None = None,
        asleep_minutes: int | None = None,
        restless_minutes: int | None = None,
        awake_minutes: int | None = None,
        minutes_to_fall_asleep: int | None = None,
        minutes_after_wakeup: int | None = None,
        time_in_bed: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sleep_sessions(
                log_id, date_of_sleep, start_time, end_time, duration_minutes,
                efficiency, is_main_sleep, log_type, sleep_type,
                deep_minutes, light_minutes, rem_minutes, wake_minutes,
                asleep_minutes, restless_minutes, awake_minutes,
                minutes_to_fall_asleep, minutes_after_wakeup, time_in_bed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(log_id) DO UPDATE SET
                date_of_sleep          = excluded.date_of_sleep,
                start_time             = excluded.start_time,
                end_time               = excluded.end_time,
                duration_minutes       = excluded.duration_minutes,
                efficiency             = excluded.efficiency,
                is_main_sleep          = excluded.is_main_sleep,
                log_type               = excluded.log_type,
                sleep_type             = excluded.sleep_type,
                deep_minutes           = excluded.deep_minutes,
                light_minutes          = excluded.light_minutes,
                rem_minutes            = excluded.rem_minutes,
                wake_minutes           = excluded.wake_minutes,
                asleep_minutes         = excluded.asleep_minutes,
                restless_minutes       = excluded.restless_minutes,
                awake_minutes          = excluded.awake_minutes,
                minutes_to_fall_asleep = excluded.minutes_to_fall_asleep,
                minutes_after_wakeup   = excluded.minutes_after_wakeup,
                time_in_bed            = excluded.time_in_bed
            """,
            (
                log_id, date_of_sleep, start_time, end_time, duration_minutes,
                efficiency, is_main_sleep, log_type, sleep_type,
                deep_minutes, light_minutes, rem_minutes, wake_minutes,
                asleep_minutes, restless_minutes, awake_minutes,
                minutes_to_fall_asleep, minutes_after_wakeup, time_in_bed,
            ),
        )
        self.conn.commit()

    def save_sleep_stages(
        self,
        log_id: int,
        stages: list[dict],
    ) -> None:
        """
        Batch-вставка интервалов стадий сна.

        stages — список dict с ключами: date_time, level, seconds, is_short (опц.)
        Перед вставкой удаляет все существующие записи для log_id.
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM sleep_stages WHERE log_id=?", (log_id,)
            )
            self.conn.executemany(
                """
                INSERT INTO sleep_stages(log_id, date_time, level, seconds, is_short)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        log_id,
                        s["date_time"],
                        s["level"],
                        s["seconds"],
                        int(s.get("is_short", False)),
                    )
                    for s in stages
                ],
            )

    def save_weight(
        self,
        date: str,
        weight_kg: float | None = None,
        bmi: float | None = None,
        fat_percent: float | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO daily_weight(date, weight_kg, bmi, fat_percent)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                weight_kg   = excluded.weight_kg,
                bmi         = excluded.bmi,
                fat_percent = excluded.fat_percent
            """,
            (date, weight_kg, bmi, fat_percent),
        )
        self.conn.commit()

    def save_hrv(
        self,
        date: str,
        rmssd: float | None = None,
        coverage: float | None = None,
        low_freq: float | None = None,
        high_freq: float | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO daily_hrv(date, rmssd, coverage, low_freq, high_freq)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                rmssd    = excluded.rmssd,
                coverage = excluded.coverage,
                low_freq = excluded.low_freq,
                high_freq= excluded.high_freq
            """,
            (date, rmssd, coverage, low_freq, high_freq),
        )
        self.conn.commit()

    def save_health_metrics(
        self,
        date: str,
        breathing_rate=None,
        spo2_avg=None,
        spo2_min=None,
        skin_temp_delta=None,
        cardio_score_min=None,
        cardio_score_max=None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO daily_health_metrics
                (date, breathing_rate, spo2_avg, spo2_min, skin_temp_delta, cardio_score_min, cardio_score_max)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                breathing_rate    = COALESCE(excluded.breathing_rate, daily_health_metrics.breathing_rate),
                spo2_avg          = COALESCE(excluded.spo2_avg, daily_health_metrics.spo2_avg),
                spo2_min          = COALESCE(excluded.spo2_min, daily_health_metrics.spo2_min),
                skin_temp_delta   = COALESCE(excluded.skin_temp_delta, daily_health_metrics.skin_temp_delta),
                cardio_score_min  = COALESCE(excluded.cardio_score_min, daily_health_metrics.cardio_score_min),
                cardio_score_max  = COALESCE(excluded.cardio_score_max, daily_health_metrics.cardio_score_max)
            """,
            (date, breathing_rate, spo2_avg, spo2_min, skin_temp_delta, cardio_score_min, cardio_score_max),
        )
        self.conn.commit()

    def save_heart_rate(
        self,
        date: str,
        resting_hr=None,
        zones: list | None = None,
    ) -> None:
        zone_map = {z["name"]: z for z in (zones or [])}

        def _min(name):
            return zone_map.get(name, {}).get("minutes")

        def _cal(name):
            return zone_map.get(name, {}).get("caloriesOut")

        self.conn.execute(
            """
            INSERT INTO daily_heart_rate
                (date, resting_hr, out_of_range_minutes, fat_burn_minutes, cardio_minutes, peak_minutes,
                 out_of_range_calories, fat_burn_calories, cardio_calories, peak_calories)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                resting_hr             = excluded.resting_hr,
                out_of_range_minutes   = excluded.out_of_range_minutes,
                fat_burn_minutes       = excluded.fat_burn_minutes,
                cardio_minutes         = excluded.cardio_minutes,
                peak_minutes           = excluded.peak_minutes,
                out_of_range_calories  = excluded.out_of_range_calories,
                fat_burn_calories      = excluded.fat_burn_calories,
                cardio_calories        = excluded.cardio_calories,
                peak_calories          = excluded.peak_calories
            """,
            (
                date, resting_hr,
                _min("Out of Range"), _min("Fat Burn"), _min("Cardio"), _min("Peak"),
                _cal("Out of Range"), _cal("Fat Burn"), _cal("Cardio"), _cal("Peak"),
            ),
        )
        self.conn.commit()

    def save_hr_intraday(self, date: str, entries: list) -> None:
        """entries: list of {"time": "HH:MM:SS", "value": int}"""
        with self.conn:
            self.conn.execute("DELETE FROM hr_intraday WHERE date = ?", (date,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO hr_intraday(date, time, bpm) VALUES (?, ?, ?)",
                [(date, e["time"], e["value"]) for e in entries],
            )

    def save_azm(
        self,
        date: str,
        fat_burn=None,
        cardio=None,
        peak=None,
    ) -> None:
        non_none = [v for v in (fat_burn, cardio, peak) if v is not None]
        total = sum(non_none) if non_none else None
        self.conn.execute(
            """
            INSERT INTO daily_azm(date, fat_burn_minutes, cardio_minutes, peak_minutes, total_minutes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                fat_burn_minutes = excluded.fat_burn_minutes,
                cardio_minutes   = excluded.cardio_minutes,
                peak_minutes     = excluded.peak_minutes,
                total_minutes    = excluded.total_minutes
            """,
            (date, fat_burn, cardio, peak, total),
        )
        self.conn.commit()

    def save_activity_log(self, date: str, activities: list) -> None:
        """activities: list of dicts from Fitbit activities array."""
        with self.conn:
            self.conn.execute("DELETE FROM activity_log WHERE date = ?", (date,))
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO activity_log
                    (log_id, date, start_time, name, duration_minutes, calories, distance_km, avg_hr, max_hr, steps)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        a["logId"],
                        date,
                        a.get("startTime", ""),
                        a.get("activityName", ""),
                        round(a.get("duration", 0) / 60000),
                        a.get("calories"),
                        round(a.get("distance", 0), 3) if a.get("distance") else None,
                        a.get("averageHeartRate"),
                        a.get("maxHeartRate") or None,
                        a.get("steps"),
                    )
                    for a in activities
                ],
            )

    def save_devices(self, devices: list) -> None:
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO devices(id, updated_at, device_version, battery, battery_level, last_sync_time, device_type)
                VALUES (?, datetime('now'), ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at      = excluded.updated_at,
                    device_version  = excluded.device_version,
                    battery         = excluded.battery,
                    battery_level   = excluded.battery_level,
                    last_sync_time  = excluded.last_sync_time,
                    device_type     = excluded.device_type
                """,
                [
                    (
                        d["id"],
                        d.get("deviceVersion"),
                        d.get("battery"),
                        d.get("batteryLevel"),
                        d.get("lastSyncTime"),
                        d.get("type"),
                    )
                    for d in devices
                ],
            )

    def save_food_log(self, date: str, entries: list[dict]) -> None:
        """
        Заменяет все записи food_log за указанную дату.

        entries — список dict с ключами: meal_type, food_name, calories,
                  protein_g, fat_g, carbs_g, amount, unit (все опциональны)
        """
        with self.conn:
            self.conn.execute("DELETE FROM food_log WHERE date=?", (date,))
            self.conn.executemany(
                """
                INSERT INTO food_log(
                    date, meal_type, food_name, calories,
                    protein_g, fat_g, carbs_g, amount, unit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        date,
                        e.get("meal_type"),
                        e.get("food_name"),
                        e.get("calories"),
                        e.get("protein_g"),
                        e.get("fat_g"),
                        e.get("carbs_g"),
                        e.get("amount"),
                        e.get("unit"),
                    )
                    for e in entries
                ],
            )

    # ==================================================================
    # CPAP — запись
    # ==================================================================

    def save_cpap_session(
        self,
        date: str,
        start_time: str | None = None,
        end_time: str | None = None,
        duration_minutes: int | None = None,
        ahi: float | None = None,
        ai: float | None = None,
        hi: float | None = None,
        obstructive_events: int | None = None,
        central_events: int | None = None,
        hypopnea_events: int | None = None,
        clear_airway_events: int | None = None,
        rera_events: int | None = None,
        leak_median: float | None = None,
        leak_95pct: float | None = None,
        pressure_min: float | None = None,
        pressure_max: float | None = None,
        pressure_median: float | None = None,
        pressure_95pct: float | None = None,
        tidal_volume_median: float | None = None,
        minute_vent_median: float | None = None,
        resp_rate_median: float | None = None,
        mask_on_off_count: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO cpap_sessions(
                date, start_time, end_time, duration_minutes, ahi, ai, hi,
                obstructive_events, central_events, hypopnea_events,
                clear_airway_events, rera_events, leak_median, leak_95pct,
                pressure_min, pressure_max, pressure_median, pressure_95pct,
                tidal_volume_median, minute_vent_median, resp_rate_median,
                mask_on_off_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                start_time          = excluded.start_time,
                end_time            = excluded.end_time,
                duration_minutes    = excluded.duration_minutes,
                ahi                 = excluded.ahi,
                ai                  = excluded.ai,
                hi                  = excluded.hi,
                obstructive_events  = excluded.obstructive_events,
                central_events      = excluded.central_events,
                hypopnea_events     = excluded.hypopnea_events,
                clear_airway_events = excluded.clear_airway_events,
                rera_events         = excluded.rera_events,
                leak_median         = excluded.leak_median,
                leak_95pct          = excluded.leak_95pct,
                pressure_min        = excluded.pressure_min,
                pressure_max        = excluded.pressure_max,
                pressure_median     = excluded.pressure_median,
                pressure_95pct      = excluded.pressure_95pct,
                tidal_volume_median = excluded.tidal_volume_median,
                minute_vent_median  = excluded.minute_vent_median,
                resp_rate_median    = excluded.resp_rate_median,
                mask_on_off_count   = excluded.mask_on_off_count
            """,
            (
                date, start_time, end_time, duration_minutes, ahi, ai, hi,
                obstructive_events, central_events, hypopnea_events,
                clear_airway_events, rera_events, leak_median, leak_95pct,
                pressure_min, pressure_max, pressure_median, pressure_95pct,
                tidal_volume_median, minute_vent_median, resp_rate_median,
                mask_on_off_count,
            ),
        )
        self.conn.commit()

    def save_cpap_events(self, date: str, events: list[dict]) -> None:
        """
        Заменяет все события CPAP за указанную дату.

        events — список dict с ключами: timestamp, event_type, duration_seconds (опц.)
        """
        with self.conn:
            self.conn.execute("DELETE FROM cpap_events WHERE date=?", (date,))
            self.conn.executemany(
                """
                INSERT INTO cpap_events(date, timestamp, event_type, duration_seconds)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (date, e["timestamp"], e["event_type"], e.get("duration_seconds"))
                    for e in events
                ],
            )

    # ==================================================================
    # O2Ring — запись
    # ==================================================================

    def save_o2ring_session(
        self,
        date: str,
        start_time: str,
        end_time: str,
        duration_minutes: int | None = None,
        avg_spo2: float | None = None,
        min_spo2: float | None = None,
        spo2_drops_count: int | None = None,
        avg_hr: float | None = None,
        min_hr: float | None = None,
        max_hr: float | None = None,
        o2_score: float | None = None,
    ) -> int:
        """
        Вставляет или обновляет сессию O2Ring. Возвращает rowid (session_id).
        Идемпотентен: при повторном импорте того же файла обновляет статистику.
        """
        existing = self.conn.execute(
            "SELECT id FROM o2ring_sessions WHERE date=? AND start_time=?",
            (date, start_time),
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE o2ring_sessions SET
                    end_time=?, duration_minutes=?, avg_spo2=?, min_spo2=?,
                    spo2_drops_count=?, avg_hr=?, min_hr=?, max_hr=?, o2_score=?
                WHERE id=?
                """,
                (
                    end_time, duration_minutes, avg_spo2, min_spo2,
                    spo2_drops_count, avg_hr, min_hr, max_hr, o2_score,
                    existing[0],
                ),
            )
            self.conn.commit()
            return existing[0]
        cursor = self.conn.execute(
            """
            INSERT INTO o2ring_sessions(
                date, start_time, end_time, duration_minutes,
                avg_spo2, min_spo2, spo2_drops_count,
                avg_hr, min_hr, max_hr, o2_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date, start_time, end_time, duration_minutes,
                avg_spo2, min_spo2, spo2_drops_count,
                avg_hr, min_hr, max_hr, o2_score,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def save_o2ring_data(self, session_id: int, data: list[dict]) -> None:
        """
        Batch-вставка 4-секундных данных O2Ring для сессии.

        data — список dict с ключами: timestamp, spo2, heart_rate, motion
        Перед вставкой удаляет все существующие записи для session_id.
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM o2ring_data WHERE session_id=?", (session_id,)
            )
            self.conn.executemany(
                """
                INSERT INTO o2ring_data(session_id, timestamp, spo2, heart_rate, motion)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        d["timestamp"],
                        d.get("spo2"),
                        d.get("heart_rate"),
                        d.get("motion"),
                    )
                    for d in data
                ],
            )

    # ==================================================================
    # Sync log
    # ==================================================================

    def upsert_sync_log(
        self,
        date: str,
        status: str,
        errors: list[str] | None = None,
    ) -> None:
        """status: 'ok' | 'partial' | 'error'"""
        now = datetime.now(timezone.utc).isoformat()
        errors_json = json.dumps(errors) if errors else None
        self.conn.execute(
            """
            INSERT INTO sync_log(date, synced_at, status, errors)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                synced_at = excluded.synced_at,
                status    = excluded.status,
                errors    = excluded.errors
            """,
            (date, now, status, errors_json),
        )
        self.conn.commit()

    # ==================================================================
    # Fitbit — чтение
    # ==================================================================

    def get_nutrition(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_nutrition WHERE date=?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def get_activity(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_activity WHERE date=?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def get_sleep_sessions(self, date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sleep_sessions WHERE date_of_sleep=? ORDER BY start_time",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_sleep_stages(self, log_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sleep_stages WHERE log_id=? ORDER BY date_time",
            (log_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_weight(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_weight WHERE date=?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def get_hrv(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_hrv WHERE date=?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def get_food_log(self, date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM food_log WHERE date=? ORDER BY meal_type, id",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_health_metrics(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_health_metrics WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def get_heart_rate(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_heart_rate WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def get_hr_intraday(self, date: str) -> list:
        rows = self.conn.execute(
            "SELECT time, bpm FROM hr_intraday WHERE date = ? ORDER BY time", (date,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_azm(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_azm WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def get_activity_log(self, date: str) -> list:
        rows = self.conn.execute(
            "SELECT * FROM activity_log WHERE date = ? ORDER BY start_time", (date,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_devices(self) -> list:
        rows = self.conn.execute(
            "SELECT * FROM devices ORDER BY device_type"
        ).fetchall()
        return [dict(r) for r in rows]

    # ==================================================================
    # CPAP — чтение
    # ==================================================================

    def get_cpap_session(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM cpap_sessions WHERE date=?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def get_cpap_events(self, date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM cpap_events WHERE date=? ORDER BY timestamp",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ==================================================================
    # O2Ring — чтение
    # ==================================================================

    def get_o2ring_session(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM o2ring_sessions WHERE date=? ORDER BY start_time LIMIT 1",
            (date,),
        ).fetchone()
        return dict(row) if row else None

    def get_o2ring_data(self, session_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM o2ring_data WHERE session_id=? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ==================================================================
    # Агрегированные / кросс-таблицы
    # ==================================================================

    def get_day(self, date: str) -> dict:
        """Возвращает все доступные данные за день из всех источников."""
        return {
            "date": date,
            "nutrition": self.get_nutrition(date),
            "activity": self.get_activity(date),
            "sleep": self.get_sleep_sessions(date),
            "weight": self.get_weight(date),
            "hrv": self.get_hrv(date),
            "cpap": self.get_cpap_session(date),
            "o2ring": self.get_o2ring_session(date),
            "food_log": self.get_food_log(date),
            "health_metrics": self.get_health_metrics(date),
            "heart_rate": self.get_heart_rate(date),
            "azm": self.get_azm(date),
            "activity_log": self.get_activity_log(date),
            "sync_status": self.get_sync_status(date),
        }

    def get_range(
        self,
        metric: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """
        Данные за диапазон дат для одного показателя.

        metric: 'nutrition' | 'activity' | 'weight' | 'hrv' | 'cpap' | 'sleep' | 'o2ring'
        """
        _table_map = {
            "nutrition": ("daily_nutrition", "date"),
            "activity": ("daily_activity", "date"),
            "weight": ("daily_weight", "date"),
            "hrv": ("daily_hrv", "date"),
            "cpap": ("cpap_sessions", "date"),
            "sleep": ("sleep_sessions", "date_of_sleep"),
            "o2ring": ("o2ring_sessions", "date"),
        }
        if metric not in _table_map:
            raise ValueError(f"Unknown metric: {metric!r}")
        table, date_col = _table_map[metric]
        rows = self.conn.execute(
            f"SELECT * FROM {table} WHERE {date_col} BETWEEN ? AND ? ORDER BY {date_col}",
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest(self, metric: str) -> str | None:
        """Возвращает последнюю дату с данными для метрики. None если данных нет."""
        _latest_map = {
            "nutrition": "SELECT MAX(date) FROM daily_nutrition",
            "activity": "SELECT MAX(date) FROM daily_activity",
            "weight": "SELECT MAX(date) FROM daily_weight",
            "hrv": "SELECT MAX(date) FROM daily_hrv",
            "cpap": "SELECT MAX(date) FROM cpap_sessions",
            "sleep": "SELECT MAX(date_of_sleep) FROM sleep_sessions",
            "o2ring": "SELECT MAX(date) FROM o2ring_sessions",
        }
        if metric not in _latest_map:
            raise ValueError(f"Unknown metric: {metric!r}")
        row = self.conn.execute(_latest_map[metric]).fetchone()
        return row[0] if row else None

    # ==================================================================
    # Sync log — чтение
    # ==================================================================

    def is_date_synced(self, source: str, date: str) -> bool:
        """True если данные за дату уже синхронизированы для указанного источника."""
        if source == "fitbit":
            row = self.conn.execute(
                "SELECT status FROM sync_log WHERE date=?", (date,)
            ).fetchone()
            return row is not None and row[0] == "ok"
        if source == "cpap":
            row = self.conn.execute(
                "SELECT id FROM cpap_sessions WHERE date=?", (date,)
            ).fetchone()
            return row is not None
        if source == "o2ring":
            row = self.conn.execute(
                "SELECT id FROM o2ring_sessions WHERE date=?", (date,)
            ).fetchone()
            return row is not None
        return False

    def get_sync_status(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sync_log WHERE date=?", (date,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result.get("errors"):
            result["errors"] = json.loads(result["errors"])
        return result
