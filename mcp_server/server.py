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

import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

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


@mcp.tool()
def get_hr_intraday(date: str) -> list[dict]:
    """
    Minute-by-minute heart rate for a date (~1440 data points).
    Each entry: {time: 'HH:MM:SS', bpm: int}.
    """
    with _db() as db:
        return db.get_hr_intraday(date)


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
def get_oximetry_data(date: str) -> list[dict]:
    """
    4-second resolution SpO2, heart rate, and motion data for a night.
    May contain up to ~9000 data points per session.
    """
    with _db() as db:
        session = db.get_o2ring_session(date)
        if session is None:
            return []
        rows = db.conn.execute(
            "SELECT timestamp, spo2, heart_rate, motion FROM o2ring_data "
            "WHERE session_id = ? ORDER BY timestamp",
            (session["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
def get_oximetry_range(start_date: str, end_date: str) -> list[dict]:
    """O2Ring session summaries for a date range — SpO2 and HR trend."""
    with _db() as db:
        return db.get_range("o2ring", start_date, end_date)


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
    Night summary combining all sleep-related data for a date:
    Fitbit sleep sessions, CPAP therapy, and O2Ring oximetry.
    """
    with _db() as db:
        return {
            "date": date,
            "sleep": db.get_sleep_sessions(date),
            "cpap": db.get_cpap_session(date),
            "o2ring": db.get_o2ring_session(date),
            "hrv": db.get_hrv(date),
            "health_metrics": db.get_health_metrics(date),
        }


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
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
