BEGIN;

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS raw_files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    date       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    filepath   TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    size_bytes INTEGER,
    UNIQUE(source, date, kind)
);
CREATE INDEX IF NOT EXISTS idx_raw_files_date ON raw_files(date, source);

CREATE TABLE IF NOT EXISTS daily_nutrition (
    date      TEXT PRIMARY KEY,
    calories  INTEGER,
    protein_g REAL,
    fat_g     REAL,
    carbs_g   REAL,
    fiber_g   REAL,
    water_ml  REAL
);

CREATE TABLE IF NOT EXISTS daily_activity (
    date                     TEXT PRIMARY KEY,
    steps                    INTEGER,
    distance_km              REAL,
    floors                   INTEGER,
    calories_burned          INTEGER,
    active_minutes_lightly   INTEGER,
    active_minutes_fairly    INTEGER,
    active_minutes_very      INTEGER,
    sedentary_minutes        INTEGER
);

CREATE TABLE IF NOT EXISTS sleep_sessions (
    log_id                  INTEGER PRIMARY KEY,
    date_of_sleep           TEXT NOT NULL,
    start_time              TEXT NOT NULL,
    end_time                TEXT NOT NULL,
    duration_minutes        INTEGER,
    efficiency              INTEGER,
    is_main_sleep           BOOLEAN,
    log_type                TEXT,
    sleep_type              TEXT,
    deep_minutes            INTEGER,
    light_minutes           INTEGER,
    rem_minutes             INTEGER,
    wake_minutes            INTEGER,
    asleep_minutes          INTEGER,
    restless_minutes        INTEGER,
    awake_minutes           INTEGER,
    minutes_to_fall_asleep  INTEGER,
    minutes_after_wakeup    INTEGER,
    time_in_bed             INTEGER
);

CREATE TABLE IF NOT EXISTS sleep_stages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id    INTEGER NOT NULL REFERENCES sleep_sessions(log_id),
    date_time TEXT NOT NULL,
    level     TEXT NOT NULL,
    seconds   INTEGER NOT NULL,
    is_short  BOOLEAN DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sleep_stages_log  ON sleep_stages(log_id);
CREATE INDEX IF NOT EXISTS idx_sleep_stages_time ON sleep_stages(date_time);

CREATE TABLE IF NOT EXISTS daily_weight (
    date        TEXT PRIMARY KEY,
    weight_kg   REAL,
    bmi         REAL,
    fat_percent REAL
);

CREATE TABLE IF NOT EXISTS daily_hrv (
    date      TEXT PRIMARY KEY,
    rmssd     REAL,
    coverage  REAL,
    low_freq  REAL,
    high_freq REAL
);

CREATE TABLE IF NOT EXISTS food_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    meal_type TEXT,
    food_name TEXT,
    calories  INTEGER,
    protein_g REAL,
    fat_g     REAL,
    carbs_g   REAL,
    amount    REAL,
    unit      TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    date      TEXT PRIMARY KEY,
    synced_at TEXT NOT NULL,
    status    TEXT NOT NULL,
    errors    TEXT
);

INSERT OR IGNORE INTO schema_version(version, applied_at, description)
VALUES (1, datetime('now'), 'initial schema: fitbit tables');

COMMIT;
