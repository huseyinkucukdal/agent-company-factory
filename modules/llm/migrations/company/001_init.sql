-- LLM module: per-company request counter (version 1).
-- Single-row table; refresh-safe persistence for the replay UI counter.

CREATE TABLE llm_request_counter (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    total           INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    rate_limited    INTEGER NOT NULL DEFAULT 0,
    last_provider   TEXT,
    last_model      TEXT,
    last_at         TEXT
);

INSERT INTO llm_request_counter (id) VALUES (1);
