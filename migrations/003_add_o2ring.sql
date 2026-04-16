BEGIN;

CREATE TABLE IF NOT EXISTS o2ring_sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT NOT NULL,
    start_time       TEXT NOT NULL,
    end_time         TEXT NOT NULL,
    duration_minutes INTEGER,
    avg_spo2         REAL,
    min_spo2         REAL,
    spo2_drops_count INTEGER,
    avg_hr           REAL,
    min_hr           REAL,
    max_hr           REAL,
    o2_score         REAL
);

CREATE TABLE IF NOT EXISTS o2ring_data (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES o2ring_sessions(id),
    timestamp  TEXT NOT NULL,
    spo2       INTEGER,
    heart_rate INTEGER,
    motion     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_o2ring_data_session ON o2ring_data(session_id);
CREATE INDEX IF NOT EXISTS idx_o2ring_data_time    ON o2ring_data(timestamp);

INSERT OR IGNORE INTO schema_version(version, applied_at, description)
VALUES (3, datetime('now'), 'add o2ring tables');

COMMIT;
