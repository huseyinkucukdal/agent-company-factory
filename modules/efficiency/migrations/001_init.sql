CREATE TABLE efficiency_findings (
    id TEXT PRIMARY KEY,
    detector_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warn', 'critical')),
    subject_type TEXT NOT NULL CHECK (
        subject_type IN (
            'agent',
            'manager',
            'company',
            'tool',
            'approval'
        )
    ),
    subject_id TEXT,
    opened_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    closed_at TEXT,
    occurrences INTEGER NOT NULL DEFAULT 1,
    evidence_json TEXT NOT NULL,
    recommendation TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (
        status IN ('open', 'acknowledged', 'closed')
    )
);
CREATE INDEX idx_findings_status ON efficiency_findings(status);
CREATE INDEX idx_findings_dedupe ON efficiency_findings(detector_code, subject_type, subject_id, status);
CREATE INDEX idx_findings_opened_at ON efficiency_findings(opened_at);