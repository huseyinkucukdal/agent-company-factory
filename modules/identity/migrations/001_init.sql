CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    persona_ref TEXT NOT NULL,
    reports_to TEXT REFERENCES agents(id),
    status TEXT NOT NULL,
    hired_at TEXT NOT NULL,
    fired_at TEXT,
    fired_by TEXT REFERENCES agents(id),
    fire_reason TEXT
);
CREATE INDEX idx_agents_reports_to ON agents(reports_to);
CREATE INDEX idx_agents_role ON agents(role);
CREATE INDEX idx_agents_status ON agents(status);
-- Pending fire requests awaiting approval. ``target_id`` is unique so a
-- second fire request for the same target is idempotent.
CREATE TABLE fire_pending (
    request_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    target_id TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    decider_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);