"""
Health Hub MCP Server

Read-only MCP server over SQLite.
Provides tools for Claude to query Fitbit + CPAP + O2Ring data.

Run:
    python -m mcp_server.server          # stdio (Claude Desktop)
    hhub-mcp                             # via entry point

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "health-hub": {
          "command": "/path/to/health-hub/.venv/bin/hhub-mcp",
          "env": {
            "DB_PATH": "/path/to/health-hub/data/health.db"
          }
        }
      }
    }
"""

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from src.db import Database

if not os.environ.get("NO_DOTENV"):
    load_dotenv(override=True)

_DB_PATH = Path(os.environ.get("DB_PATH", "data/health.db"))

mcp = FastMCP(
    "Health Hub",
    instructions=(
        "Read-only access to personal health data: Fitbit (nutrition, activity, sleep, "
        "weight, HRV, heart rate), CPAP therapy, and O2Ring oximetry. "
        "All dates are YYYY-MM-DD strings. "
        "Tools return None/empty list when no data is available for the requested date."
    ),
)


def _db() -> "Database":
    """
    Open a fresh read-only SQLite connection per MCP tool call.

    FastMCP may dispatch tools from worker threads; sqlite3 connections are
    not safe to share across threads by default, and a shared writer-mode
    connection inside an MCP process also risks contention with the collector
    running in another process. Opening `mode=ro` via URI plus WAL on the
    writer side lets readers and writers coexist without locking.

    Write attempts through this connection raise `sqlite3.OperationalError`.
    """
    from src.db import Database
    return Database(_DB_PATH, readonly=True)


# ===========================================================================
# Fitbit tools
# ===========================================================================

@mcp.tool()
def get_nutrition(date: str) -> dict | None:
    """Daily nutrition summary (calories, protein, fat, carbs, water) for a date."""
    with _db() as db:
        return db.get_nutrition(date)


@mcp.tool()
def get_food_log(date: str) -> list[dict]:
    """Detailed food log for a date — individual items with name, meal type, and macros."""
    with _db() as db:
        return db.get_food_log(date)


@mcp.tool()
def search_food_log(query: str, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """
    Search food log entries by name (case-insensitive substring match).
    Optionally filter by date range.
    """
    with _db() as db:
        sql = "SELECT * FROM food_log WHERE LOWER(food_name) LIKE LOWER(?)"
        params: list = [f"%{query}%"]
        if start_date:
            sql += " AND date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND date <= ?"
            params.append(end_date)
        sql += " ORDER BY date DESC, meal_type, id"
        rows = db.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
def get_activity(date: str) -> dict | None:
    """Daily activity summary (steps, distance, calories burned, active minutes) for a date."""
    with _db() as db:
        return db.get_activity(date)


@mcp.tool()
def get_activity_log(date: str) -> list[dict]:
    """Individual workout sessions for a date (name, duration, calories, HR)."""
    with _db() as db:
        return db.get_activity_log(date)


@mcp.tool()
def get_sleep(date: str) -> list[dict]:
    """All sleep sessions for a date with summary stats (duration, efficiency, stages)."""
    with _db() as db:
        return db.get_sleep_sessions(date)


@mcp.tool()
def get_sleep_stages(log_id: int) -> list[dict]:
    """
    Full hypnogram for a sleep session — 30-second intervals with stage labels.
    Use log_id from get_sleep() results.
    Stages: deep, light, rem, wake (or asleep, restless, awake for classic tracker).
    """
    with _db() as db:
        return db.get_sleep_stages(log_id)


@mcp.tool()
def get_sleep_range(start_date: str, end_date: str) -> list[dict]:
    """Sleep sessions for a date range — useful for analysing trends."""
    with _db() as db:
        return db.get_range("sleep", start_date, end_date)


@mcp.tool()
def get_weight(date: str) -> dict | None:
    """Weight entry for a date (kg, BMI, body fat %)."""
    with _db() as db:
        return db.get_weight(date)


@mcp.tool()
def get_hrv(date: str) -> dict | None:
    """Heart rate variability for a date (rMSSD, coverage, low/high frequency)."""
    with _db() as db:
        return db.get_hrv(date)


@mcp.tool()
def get_heart_rate(date: str) -> dict | None:
    """Daily heart rate summary: resting HR and time in each zone (fat burn, cardio, peak)."""
    with _db() as db:
        return db.get_heart_rate(date)


_MAX_PAGE = 2000
_DEFAULT_PAGE = 500


def _page(limit: int | None, offset: int | None) -> tuple[int, int]:
    """Clamp (limit, offset) to safe bounds: 1 ≤ limit ≤ _MAX_PAGE, offset ≥ 0."""
    eff_limit = _DEFAULT_PAGE if limit is None else max(1, min(int(limit), _MAX_PAGE))
    eff_offset = 0 if offset is None else max(0, int(offset))
    return eff_limit, eff_offset


@mcp.tool()
def get_hr_intraday(
    date: str,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict]:
    """
    Minute-by-minute heart rate for a date (~1440 data points per full day).
    Paginated: default limit 500, max 2000. Use `offset` to walk the day.
    Each entry: {time: 'HH:MM:SS', bpm: int}.
    """
    eff_limit, eff_offset = _page(limit, offset)
    with _db() as db:
        rows = db.conn.execute(
            "SELECT time, bpm FROM hr_intraday WHERE date = ? "
            "ORDER BY time LIMIT ? OFFSET ?",
            (date, eff_limit, eff_offset),
        ).fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
def get_health_metrics(date: str) -> dict | None:
    """
    Advanced health metrics for a date: breathing rate, SpO2 (nightly avg/min),
    skin temperature delta, and cardio fitness score (VO2 max range).
    Returns None if device doesn't support these metrics.
    """
    with _db() as db:
        return db.get_health_metrics(date)


@mcp.tool()
def get_azm(date: str) -> dict | None:
    """Active Zone Minutes for a date (fat burn, cardio, peak, total)."""
    with _db() as db:
        return db.get_azm(date)


# ===========================================================================
# CPAP tools
# ===========================================================================

@mcp.tool()
def get_cpap_session(date: str) -> dict | None:
    """
    CPAP therapy summary for a night: AHI, event counts (obstructive/central/hypopnea),
    leak statistics, and pressure range.
    """
    with _db() as db:
        return db.get_cpap_session(date)


@mcp.tool()
def get_cpap_events(date: str) -> list[dict]:
    """Individual apnea events for a night with timestamps and types."""
    with _db() as db:
        return db.get_cpap_events(date)


@mcp.tool()
def get_cpap_range(start_date: str, end_date: str) -> list[dict]:
    """CPAP session summaries for a date range — useful for AHI trend analysis."""
    with _db() as db:
        return db.get_range("cpap", start_date, end_date)


# ===========================================================================
# O2Ring tools
# ===========================================================================

@mcp.tool()
def get_oximetry(date: str) -> dict | None:
    """
    Overnight oximetry summary: avg/min SpO2, desaturation count, avg/min/max HR.
    """
    with _db() as db:
        return db.get_o2ring_session(date)


@mcp.tool()
def get_oximetry_data(
    date: str,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict]:
    """
    4-second resolution SpO2, heart rate, and motion data for a night.
    Up to ~9000 data points per session. Paginated: default limit 500,
    max 2000. Use `offset` to walk the session.
    """
    eff_limit, eff_offset = _page(limit, offset)
    with _db() as db:
        session = db.get_o2ring_session(date)
        if session is None:
            return []
        rows = db.conn.execute(
            "SELECT timestamp, spo2, heart_rate, motion FROM o2ring_data "
            "WHERE session_id = ? ORDER BY timestamp LIMIT ? OFFSET ?",
            (session["id"], eff_limit, eff_offset),
        ).fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
def get_oximetry_range(start_date: str, end_date: str) -> list[dict]:
    """O2Ring session summaries for a date range — SpO2 and HR trend."""
    with _db() as db:
        return db.get_range("o2ring", start_date, end_date)


# ===========================================================================
# Health Connect tools (records pushed by Android Health Connect Bridge)
#
# All Health Connect records live in `hc_records`. `data_json` holds the
# full payload as received; nested fields (SleepSession.stages,
# Nutrition macros, etc.) are queried via SQLite json_extract.
#
# Date convention: `hc_records.date` is the local date of start_time.
# Sleep sessions starting at 22:30 land under that evening's date, so
# helpers that want "sleep ending on wake_date D" filter on end_time's
# local date via the `localtime` modifier (relies on container TZ env).
# ===========================================================================

@mcp.tool()
def get_hc_sleep(wake_date: str) -> list[dict]:
    """
    Sleep sessions whose local end time falls on `wake_date` (YYYY-MM-DD).
    Each result includes a per-stage breakdown in minutes (deep/light/rem/awake)
    plus total duration and the source app. The raw `stages` array (variable-
    length intervals with start/end) is returned as `stages_raw` for callers
    that want the full hypnogram.
    """
    with _db() as db:
        rows = db.conn.execute(
            """
            SELECT uid, start_time, end_time, source_app, source_device, data_json,
                   CAST((julianday(end_time) - julianday(start_time)) * 24 * 60 AS REAL)
                       AS total_minutes
            FROM hc_records
            WHERE type='SleepSession'
              AND date(end_time, 'localtime') = ?
            ORDER BY start_time
            """,
            (wake_date,),
        ).fetchall()

        result = []
        for r in rows:
            data = json.loads(r["data_json"])
            stages_raw = data.get("stages") or []
            by_stage: dict[str, float] = {}
            for s in stages_raw:
                stage = s.get("stage")
                if not stage:
                    continue
                start = s.get("start") or s.get("startTime")
                end = s.get("end") or s.get("endTime")
                if not (start and end):
                    continue
                # Use sqlite to get robust julianday math without re-parsing in Python
                mins = db.conn.execute(
                    "SELECT (julianday(?) - julianday(?)) * 24 * 60",
                    (end, start),
                ).fetchone()[0] or 0
                by_stage[stage] = round(by_stage.get(stage, 0) + mins, 1)

            result.append({
                "uid": r["uid"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "total_minutes": round(r["total_minutes"], 1),
                "source_app": r["source_app"],
                "source_device": r["source_device"],
                "stages_minutes": by_stage,
                "stages_raw": stages_raw,
            })
        return result


@mcp.tool()
def get_hc_sleep_range(start_date: str, end_date: str) -> list[dict]:
    """
    Sleep sessions for a wake-date range (inclusive). Returns per-night
    totals: date, total_minutes, deep_minutes, light_minutes, rem_minutes,
    awake_minutes, source_app. Useful for trend analysis.
    """
    with _db() as db:
        rows = db.conn.execute(
            """
            SELECT uid, start_time, end_time, source_app, data_json,
                   date(end_time, 'localtime') AS wake_date
            FROM hc_records
            WHERE type='SleepSession'
              AND date(end_time, 'localtime') BETWEEN ? AND ?
            ORDER BY end_time
            """,
            (start_date, end_date),
        ).fetchall()

        out = []
        for r in rows:
            data = json.loads(r["data_json"])
            by_stage: dict[str, float] = {"deep": 0, "light": 0, "rem": 0, "awake": 0}
            for s in (data.get("stages") or []):
                stage = s.get("stage")
                if stage not in by_stage:
                    continue
                start = s.get("start") or s.get("startTime")
                end = s.get("end") or s.get("endTime")
                if not (start and end):
                    continue
                mins = db.conn.execute(
                    "SELECT (julianday(?) - julianday(?)) * 24 * 60",
                    (end, start),
                ).fetchone()[0] or 0
                by_stage[stage] = round(by_stage[stage] + mins, 1)
            total = db.conn.execute(
                "SELECT (julianday(?) - julianday(?)) * 24 * 60",
                (r["end_time"], r["start_time"]),
            ).fetchone()[0] or 0
            out.append({
                "wake_date": r["wake_date"],
                "uid": r["uid"],
                "total_minutes": round(total, 1),
                "deep_minutes": by_stage["deep"],
                "light_minutes": by_stage["light"],
                "rem_minutes": by_stage["rem"],
                "awake_minutes": by_stage["awake"],
                "source_app": r["source_app"],
            })
        return out


@mcp.tool()
def get_hc_hrv(date: str) -> dict | None:
    """HRV (rMSSD) daily summary from Health Connect — avg/min/max + sample count."""
    with _db() as db:
        row = db.conn.execute(
            "SELECT * FROM daily_hc_hrv WHERE date = ?",
            (date,),
        ).fetchone()
        return dict(row) if row else None


@mcp.tool()
def get_hc_hrv_range(start_date: str, end_date: str) -> list[dict]:
    """HRV trend (daily avg/min/max rMSSD) over a date range."""
    with _db() as db:
        rows = db.conn.execute(
            "SELECT * FROM daily_hc_hrv WHERE date BETWEEN ? AND ? ORDER BY date",
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
def get_hc_resting_hr(date: str) -> dict | None:
    """Resting heart rate from Health Connect for a date (daily avg)."""
    with _db() as db:
        row = db.conn.execute(
            "SELECT * FROM daily_hc_resting_hr WHERE date = ?",
            (date,),
        ).fetchone()
        return dict(row) if row else None


@mcp.tool()
def get_hc_heart_rate_summary(date: str) -> dict | None:
    """Heart rate aggregates (avg/min/max + sample count) over all HR samples for a date."""
    with _db() as db:
        row = db.conn.execute(
            """
            SELECT COUNT(*) AS samples,
                   ROUND(AVG(value), 1) AS avg_bpm,
                   MIN(value) AS min_bpm,
                   MAX(value) AS max_bpm
            FROM hc_records WHERE type='HeartRate' AND date = ?
            """,
            (date,),
        ).fetchone()
        if not row or row["samples"] == 0:
            return None
        return dict(row)


@mcp.tool()
def get_hc_spo2(date: str) -> dict | None:
    """SpO2 (oxygen saturation) aggregates for a date — avg/min/max + sample count."""
    with _db() as db:
        row = db.conn.execute(
            """
            SELECT COUNT(*) AS samples,
                   ROUND(AVG(value), 1) AS avg_spo2,
                   MIN(value) AS min_spo2,
                   MAX(value) AS max_spo2
            FROM hc_records WHERE type='OxygenSaturation' AND date = ?
            """,
            (date,),
        ).fetchone()
        if not row or row["samples"] == 0:
            return None
        return dict(row)


@mcp.tool()
def get_hc_skin_temp(date: str) -> dict | None:
    """Skin temperature daily summary from Health Connect (avg/min/max)."""
    with _db() as db:
        row = db.conn.execute(
            "SELECT * FROM daily_hc_skin_temp WHERE date = ?",
            (date,),
        ).fetchone()
        return dict(row) if row else None


@mcp.tool()
def get_hc_skin_temp_range(start_date: str, end_date: str) -> list[dict]:
    """Skin temperature trend over a date range."""
    with _db() as db:
        rows = db.conn.execute(
            "SELECT * FROM daily_hc_skin_temp WHERE date BETWEEN ? AND ? ORDER BY date",
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
def get_hc_steps(date: str) -> int | None:
    """Total steps for a date from Health Connect (sum across all Steps records)."""
    with _db() as db:
        row = db.conn.execute(
            "SELECT SUM(value) AS total FROM hc_records WHERE type='Steps' AND date = ?",
            (date,),
        ).fetchone()
        return int(row["total"]) if row and row["total"] is not None else None


@mcp.tool()
def get_hc_distance(date: str) -> float | None:
    """Total distance for a date (sum of Distance records, unit comes from the records)."""
    with _db() as db:
        row = db.conn.execute(
            "SELECT SUM(value) AS total, unit FROM hc_records "
            "WHERE type='Distance' AND date = ? GROUP BY unit",
            (date,),
        ).fetchone()
        return float(row["total"]) if row and row["total"] is not None else None


@mcp.tool()
def get_hc_calories(date: str) -> float | None:
    """Total calories burned for a date (sum of TotalCaloriesBurned records)."""
    with _db() as db:
        row = db.conn.execute(
            "SELECT SUM(value) AS total FROM hc_records "
            "WHERE type='TotalCaloriesBurned' AND date = ?",
            (date,),
        ).fetchone()
        return float(row["total"]) if row and row["total"] is not None else None


@mcp.tool()
def get_hc_floors(date: str) -> int | None:
    """Floors climbed for a date (sum of FloorsClimbed records)."""
    with _db() as db:
        row = db.conn.execute(
            "SELECT SUM(value) AS total FROM hc_records "
            "WHERE type='FloorsClimbed' AND date = ?",
            (date,),
        ).fetchone()
        return int(row["total"]) if row and row["total"] is not None else None


@mcp.tool()
def get_hc_weight(date: str) -> dict | None:
    """Most recent Weight record on or before `date` (HC scalar value in kg)."""
    with _db() as db:
        row = db.conn.execute(
            "SELECT start_time, value, unit, source_app FROM hc_records "
            "WHERE type='Weight' AND date <= ? ORDER BY start_time DESC LIMIT 1",
            (date,),
        ).fetchone()
        return dict(row) if row else None


@mcp.tool()
def get_hc_weight_range(start_date: str, end_date: str) -> list[dict]:
    """
    Weight records over a date range (inclusive) from Health Connect.
    Each entry: {date, start_time, value, unit, source_app}. Useful for
    weight-trend analysis when Fitbit's `daily_weight` is empty (Health
    Connect is the primary source).
    """
    with _db() as db:
        rows = db.conn.execute(
            "SELECT date, start_time, value, unit, source_app FROM hc_records "
            "WHERE type='Weight' AND date BETWEEN ? AND ? ORDER BY start_time",
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
def get_hc_body_composition(date: str) -> dict:
    """
    Body composition snapshot for a date: BodyFat %, BodyWaterMass, BoneMass,
    Height. Returns the most recent record of each type on or before `date`.
    """
    out: dict = {}
    with _db() as db:
        for t in ("BodyFat", "BodyWaterMass", "BoneMass", "Height"):
            row = db.conn.execute(
                "SELECT start_time, value, unit, source_app FROM hc_records "
                "WHERE type=? AND date <= ? ORDER BY start_time DESC LIMIT 1",
                (t, date),
            ).fetchone()
            if row:
                out[t] = dict(row)
    return out


@mcp.tool()
def get_hc_body_composition_range(start_date: str, end_date: str) -> list[dict]:
    """
    Body composition samples over a date range (inclusive). Returns the raw
    per-sample time series for BodyFat, BodyWaterMass, BoneMass, and Height
    so callers can chart any of them independently.
    Each entry: {date, type, start_time, value, unit, source_app}.
    """
    with _db() as db:
        rows = db.conn.execute(
            "SELECT date, type, start_time, value, unit, source_app FROM hc_records "
            "WHERE type IN ('BodyFat','BodyWaterMass','BoneMass','Height') "
            "  AND date BETWEEN ? AND ? "
            "ORDER BY start_time",
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
def get_hc_nutrition(date: str) -> list[dict]:
    """
    Nutrition entries for a date (each meal/snack with whatever macros the
    source app supplied — see metadata in each record's data_json).
    """
    with _db() as db:
        rows = db.conn.execute(
            "SELECT uid, start_time, source_app, data_json FROM hc_records "
            "WHERE type='Nutrition' AND date = ? ORDER BY start_time",
            (date,),
        ).fetchall()
        return [
            {**{k: r[k] for k in ("uid", "start_time", "source_app")},
             "data": json.loads(r["data_json"])}
            for r in rows
        ]


@mcp.tool()
def get_hc_exercises(date: str) -> list[dict]:
    """Exercise sessions for a date (workouts with start/end and any extra payload)."""
    with _db() as db:
        rows = db.conn.execute(
            """
            SELECT uid, start_time, end_time, source_app, data_json,
                   CAST((julianday(end_time) - julianday(start_time)) * 24 * 60 AS REAL)
                       AS minutes
            FROM hc_records WHERE type='ExerciseSession' AND date = ?
            ORDER BY start_time
            """,
            (date,),
        ).fetchall()
        return [
            {"uid": r["uid"], "start_time": r["start_time"], "end_time": r["end_time"],
             "minutes": round(r["minutes"], 1) if r["minutes"] else None,
             "source_app": r["source_app"], "data": json.loads(r["data_json"])}
            for r in rows
        ]


@mcp.tool()
def get_hc_records(
    date: str,
    type: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict]:
    """
    Raw Health Connect records for a date. Filter by `type` (e.g. "BloodPressure",
    "RespiratoryRate") to drill into a specific data type. Paginated.
    """
    eff_limit, eff_offset = _page(limit, offset)
    sql = ("SELECT uid, type, start_time, end_time, value, unit, source_app, "
           "source_device, data_json FROM hc_records WHERE date = ?")
    params: list = [date]
    if type:
        sql += " AND type = ?"
        params.append(type)
    sql += " ORDER BY start_time LIMIT ? OFFSET ?"
    params += [eff_limit, eff_offset]
    with _db() as db:
        rows = db.conn.execute(sql, params).fetchall()
        return [
            {**{k: r[k] for k in ("uid", "type", "start_time", "end_time",
                                  "value", "unit", "source_app", "source_device")},
             "data": json.loads(r["data_json"])}
            for r in rows
        ]


@mcp.tool()
def get_hc_record_types(date: str | None = None) -> list[dict]:
    """
    All record types present in `hc_records` with counts. Without `date` returns
    the all-time totals; with `date` returns only that date. Useful as a first
    call to see what data exists before drilling in.
    """
    with _db() as db:
        if date:
            rows = db.conn.execute(
                "SELECT type, COUNT(*) AS count FROM hc_records WHERE date = ? "
                "GROUP BY type ORDER BY count DESC",
                (date,),
            ).fetchall()
        else:
            rows = db.conn.execute(
                "SELECT type, COUNT(*) AS count, MIN(start_time) AS earliest, "
                "MAX(start_time) AS latest FROM hc_records "
                "GROUP BY type ORDER BY count DESC"
            ).fetchall()
        return [dict(r) for r in rows]


# ===========================================================================
# Cross-source tools
# ===========================================================================

@mcp.tool()
def get_day_summary(date: str) -> dict:
    """
    Everything available for a date from all sources in one call:
    nutrition, food_log, activity, sleep, weight, hrv, heart_rate,
    health_metrics, azm, cpap, o2ring.
    """
    with _db() as db:
        return db.get_day(date)


@mcp.tool()
def get_night_summary(date: str) -> dict:
    """
    Night summary combining all sleep-related data for a wake date:
    Fitbit sleep sessions, Health Connect sleep (with stages), CPAP
    therapy, O2Ring oximetry, HRV (Fitbit + HC), and HC SpO2/skin temp.
    """
    with _db() as db:
        result = {
            "date": date,
            "fitbit_sleep": db.get_sleep_sessions(date),
            "cpap": db.get_cpap_session(date),
            "o2ring": db.get_o2ring_session(date),
            "fitbit_hrv": db.get_hrv(date),
            "fitbit_health_metrics": db.get_health_metrics(date),
        }
    # HC tools open their own connections — call them outside the with-block.
    result["hc_sleep"] = get_hc_sleep(date)
    result["hc_hrv"] = get_hc_hrv(date)
    result["hc_spo2"] = get_hc_spo2(date)
    result["hc_skin_temp"] = get_hc_skin_temp(date)
    result["hc_resting_hr"] = get_hc_resting_hr(date)
    return result


@mcp.tool()
def get_range(metric: str, start_date: str, end_date: str) -> list[dict]:
    """
    Data for a metric over a date range.
    metric: nutrition | activity | weight | hrv | cpap | sleep | o2ring
    """
    with _db() as db:
        return db.get_range(metric, start_date, end_date)


@mcp.tool()
def get_status() -> dict:
    """
    Database status: data coverage ranges and last sync for each source.
    Useful to know what date range is available before querying.
    """
    if not _DB_PATH.exists():
        return {"error": f"Database not found: {_DB_PATH}"}

    with _db() as db:
        result: dict = {"db_path": str(_DB_PATH), "sources": {}}

        queries = {
            "fitbit_activity": ("daily_activity", "date"),
            "fitbit_sleep":    ("sleep_sessions", "date_of_sleep"),
            "fitbit_nutrition":("daily_nutrition", "date"),
            "cpap":            ("cpap_sessions", "date"),
            "o2ring":          ("o2ring_sessions", "date"),
        }
        for key, (table, col) in queries.items():
            row = db.conn.execute(
                f"SELECT MIN({col}), MAX({col}), COUNT(DISTINCT {col}) FROM {table}"
            ).fetchone()
            if row and row[0]:
                result["sources"][key] = {
                    "first": row[0], "last": row[1], "days": row[2]
                }

        # Health Connect overview: per-type counts + global date range.
        hc_range = db.conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM hc_records"
        ).fetchone()
        if hc_range and hc_range[0]:
            hc_types = db.conn.execute(
                "SELECT type, COUNT(*) AS count FROM hc_records "
                "GROUP BY type ORDER BY count DESC"
            ).fetchall()
            result["sources"]["health_connect"] = {
                "first": hc_range[0],
                "last": hc_range[1],
                "days": hc_range[2],
                "types": {r["type"]: r["count"] for r in hc_types},
            }

        last_sync = db.conn.execute(
            "SELECT date, synced_at, status FROM sync_log ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if last_sync:
            result["last_sync"] = dict(last_sync)

    return result


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport in ("sse", "streamable-http"):
        from mcp.server.transport_security import TransportSecuritySettings

        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8766"))

        # FastMCP auto-enables DNS-rebinding protection at construction time
        # because the default host is 127.0.0.1; overriding host afterwards
        # leaves the localhost-only allow-list in place and remote clients
        # get 421 "Invalid Host header". Set explicit allow-list (or disable
        # entirely) for LAN access.
        allowed = os.environ.get("MCP_ALLOWED_HOSTS", "*").strip()
        if allowed == "*":
            # Disable host-header check — safe behind a firewall / private LAN
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            )
        else:
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[h.strip() for h in allowed.split(",") if h.strip()],
            )

    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
