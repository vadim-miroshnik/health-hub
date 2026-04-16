BEGIN;

CREATE TABLE IF NOT EXISTS cpap_sessions (
    date                 TEXT PRIMARY KEY,
    start_time           TEXT,
    end_time             TEXT,
    duration_minutes     INTEGER,
    ahi                  REAL,
    ai                   REAL,
    hi                   REAL,
    obstructive_events   INTEGER,
    central_events       INTEGER,
    hypopnea_events      INTEGER,
    clear_airway_events  INTEGER,
    rera_events          INTEGER,
    leak_median          REAL,
    leak_95pct           REAL,
    pressure_min         REAL,
    pressure_max         REAL,
    pressure_median      REAL,
    pressure_95pct       REAL,
    tidal_volume_median  REAL,
    minute_vent_median   REAL,
    resp_rate_median     REAL,
    mask_on_off_count    INTEGER
);

CREATE TABLE IF NOT EXISTS cpap_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    duration_seconds REAL
);
CREATE INDEX IF NOT EXISTS idx_cpap_events_date ON cpap_events(date);

INSERT OR IGNORE INTO schema_version(version, applied_at, description)
VALUES (2, datetime('now'), 'add cpap tables');

COMMIT;
