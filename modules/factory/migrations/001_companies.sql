CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mission TEXT,
    industry TEXT,
    spec_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);