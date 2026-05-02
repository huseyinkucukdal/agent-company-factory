CREATE TABLE feedback (
    id TEXT PRIMARY KEY,
    target_agent_id TEXT NOT NULL REFERENCES agents(id),
    from_agent_id TEXT NOT NULL REFERENCES agents(id),
    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    note TEXT NOT NULL,
    ts TEXT NOT NULL
);

CREATE INDEX idx_feedback_target_ts ON feedback(target_agent_id, ts DESC);
CREATE INDEX idx_feedback_from_ts ON feedback(from_agent_id, ts DESC);
