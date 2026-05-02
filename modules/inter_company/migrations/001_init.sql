-- Module 16 — Inter-Company Communication, board-level outbox.
--
-- Cross-company runtime message log. One row per send_cross call.
-- Status values:
--   delivered — handed off to target.orchestrator.system_send (QUEUED)
--   queued    — target was paused at send time; awaits deliver_pending
--   rejected  — scope/rate/target failure (reason populated)
CREATE TABLE inter_company_outbox (
    id TEXT PRIMARY KEY,
    link_id TEXT NOT NULL,
    from_company TEXT NOT NULL,
    to_company TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    target_agent TEXT,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE INDEX idx_outbox_link ON inter_company_outbox(link_id, created_at);
CREATE INDEX idx_outbox_target_status ON inter_company_outbox(to_company, status);
CREATE INDEX idx_outbox_status_ts ON inter_company_outbox(status, created_at);