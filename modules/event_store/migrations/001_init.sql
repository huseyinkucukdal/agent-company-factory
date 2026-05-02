-- Event Store schema (version 1).
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    actor_agent_id TEXT,
    correlation_id TEXT,
    ts_company TEXT NOT NULL,
    ts_real TEXT NOT NULL
);
CREATE INDEX idx_events_kind ON events(kind);
CREATE INDEX idx_events_actor ON events(actor_agent_id);
CREATE INDEX idx_events_correlation ON events(correlation_id);
CREATE INDEX idx_events_ts ON events(ts_real);