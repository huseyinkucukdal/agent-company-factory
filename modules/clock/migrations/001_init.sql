-- Clock schema (version 1).
CREATE TABLE clock_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL,
    epoch_real TEXT NOT NULL,
    epoch_company TEXT NOT NULL,
    paused_total_sec REAL NOT NULL DEFAULT 0,
    pause_started_at TEXT,
    last_day_ticked INTEGER NOT NULL DEFAULT 0,
    rate_seconds_per_day REAL NOT NULL
);