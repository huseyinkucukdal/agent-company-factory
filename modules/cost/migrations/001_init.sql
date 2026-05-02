CREATE TABLE expenses (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    amount_units INTEGER NOT NULL,
    category TEXT NOT NULL,
    memo TEXT,
    approval_id TEXT,
    ts_company TEXT NOT NULL,
    ts_real TEXT NOT NULL
);
CREATE INDEX idx_expenses_category ON expenses(category);
CREATE INDEX idx_expenses_agent ON expenses(agent_id);
CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    amount_units INTEGER NOT NULL,
    ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'committed', 'released')),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_reservations_status ON reservations(status);
CREATE TABLE subscriptions (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    amount_units INTEGER NOT NULL,
    category TEXT NOT NULL,
    next_charge_company_day INTEGER NOT NULL,
    period_days INTEGER NOT NULL DEFAULT 30,
    cancelled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_subs_due ON subscriptions(next_charge_company_day)
WHERE cancelled = 0;
CREATE TABLE budget_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total_units INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    last_warn_pct REAL NOT NULL DEFAULT 0
);
INSERT INTO budget_state (id, total_units, blocked, last_warn_pct)
VALUES (1, 0, 0, 0);