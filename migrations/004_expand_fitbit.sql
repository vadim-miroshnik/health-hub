BEGIN;

INSERT OR IGNORE INTO schema_version(version, applied_at, description)
VALUES (4, datetime('now'), 'Expand Fitbit: health metrics, HR intraday, AZM, activity log, devices');

-- Health metrics (breathing rate, SpO2, skin temp, cardio fitness score)
CREATE TABLE IF NOT EXISTS daily_health_metrics (
    date TEXT PRIMARY KEY,
    breathing_rate REAL,
    spo2_avg REAL,
    spo2_min REAL,
    skin_temp_delta REAL,
    cardio_score_min REAL,
    cardio_score_max REAL
);

-- Daily heart rate summary (resting HR + zones)
CREATE TABLE IF NOT EXISTS daily_heart_rate (
    date TEXT PRIMARY KEY,
    resting_hr INTEGER,
    out_of_range_minutes INTEGER,
    fat_burn_minutes INTEGER,
    cardio_minutes INTEGER,
    peak_minutes INTEGER,
    out_of_range_calories REAL,
    fat_burn_calories REAL,
    cardio_calories REAL,
    peak_calories REAL
);

-- Intraday heart rate 1-min resolution (~1440 rows/day, ~2.6M rows/5yr)
CREATE TABLE IF NOT EXISTS hr_intraday (
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    bpm INTEGER,
    PRIMARY KEY (date, time)
);
CREATE INDEX IF NOT EXISTS idx_hr_intraday_date ON hr_intraday(date);

-- Active Zone Minutes
CREATE TABLE IF NOT EXISTS daily_azm (
    date TEXT PRIMARY KEY,
    fat_burn_minutes INTEGER,
    cardio_minutes INTEGER,
    peak_minutes INTEGER,
    total_minutes INTEGER
);

-- Activity log (workouts with details) — extracted from existing activity.json
CREATE TABLE IF NOT EXISTS activity_log (
    log_id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    name TEXT,
    duration_minutes INTEGER,
    calories INTEGER,
    distance_km REAL,
    avg_hr INTEGER,
    max_hr INTEGER,
    steps INTEGER
);
CREATE INDEX IF NOT EXISTS idx_activity_log_date ON activity_log(date);

-- Devices (battery monitoring)
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    device_version TEXT,
    battery TEXT,
    battery_level INTEGER,
    last_sync_time TEXT,
    device_type TEXT
);

COMMIT;
