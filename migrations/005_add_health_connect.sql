-- Migration 005 — Health Connect (Phase 10)
--
-- Universal hc_records table for ANY Health Connect type pushed in from the
-- Android Health Connect Bridge. Dedup key is the HC-supplied `uid` (ON
-- CONFLICT DO NOTHING keeps the first copy).
--
-- Timezone: `start_time` / `end_time` are ISO8601 with tz (UTC 'Z' from HC).
-- `date` is the LOCAL wall-clock date derived at ingest time from
-- start_time, per CLAUDE.md "Timezone policy" — used for fast daily joins
-- with Fitbit / CPAP / O2Ring tables that share the same local-date
-- convention.

BEGIN;

INSERT OR IGNORE INTO schema_version(version, applied_at, description)
VALUES (5, datetime('now'), 'Health Connect universal records table + views');

CREATE TABLE IF NOT EXISTS hc_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL,
    unit TEXT,
    source_app TEXT,
    source_device TEXT,
    data_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hc_records_date_type ON hc_records(date, type);
CREATE INDEX IF NOT EXISTS idx_hc_records_type_time ON hc_records(type, start_time);

-- Convenience views for frequently-queried metrics.
CREATE VIEW IF NOT EXISTS daily_hc_hrv AS
SELECT
    date,
    AVG(value) AS avg_rmssd,
    MIN(value) AS min_rmssd,
    MAX(value) AS max_rmssd,
    COUNT(*)   AS measurements
FROM hc_records
WHERE type = 'HeartRateVariabilityRmssd'
GROUP BY date;

CREATE VIEW IF NOT EXISTS daily_hc_skin_temp AS
SELECT
    date,
    AVG(value) AS avg_temp,
    MIN(value) AS min_temp,
    MAX(value) AS max_temp
FROM hc_records
WHERE type = 'SkinTemperature'
GROUP BY date;

CREATE VIEW IF NOT EXISTS daily_hc_resting_hr AS
SELECT
    date,
    AVG(value) AS avg_resting_hr
FROM hc_records
WHERE type = 'RestingHeartRate'
GROUP BY date;

COMMIT;
