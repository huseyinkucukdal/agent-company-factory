CREATE TABLE approvals (
    request_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    route_target TEXT NOT NULL,
    require_security INTEGER NOT NULL DEFAULT 0,
    security_decision TEXT CHECK (
        security_decision IN ('approve', 'deny')
        OR security_decision IS NULL
    ),
    security_decided_at TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'pending',
            'approved',
            'denied',
            'timeout',
            'cancelled'
        )
    ),
    decided_by TEXT,
    decided_at TEXT,
    note TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_approvals_status ON approvals(status);
CREATE INDEX idx_approvals_expires ON approvals(expires_at);
CREATE INDEX idx_approvals_requester ON approvals(requester_id);
CREATE INDEX idx_approvals_route ON approvals(route_target);