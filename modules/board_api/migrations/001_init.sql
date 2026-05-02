-- Board-level schema owned by Module 14 (Board Backend API).
--
-- Tables:
--   * users               — board operators (humans), JWT subject
--   * refresh_tokens      — hashed refresh-token store with revoke flag
--   * company_assignments — operator scope: which companies an operator owns
--   * board_audit         — every state-changing board API call
--   * board_links         — admin-managed inter-company links
--                          (cross-company message routing arrives with
--                          Module 16 — Inter-Company Communication)

CREATE TABLE users (
    id            TEXT    PRIMARY KEY,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    last_login    TEXT,
    deleted       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_users_role    ON users(role);
CREATE INDEX idx_users_deleted ON users(deleted);

CREATE TABLE refresh_tokens (
    id          TEXT    PRIMARY KEY,
    user_id     TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT    NOT NULL UNIQUE,
    expires_at  TEXT    NOT NULL,
    issued_at   TEXT    NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_refresh_user    ON refresh_tokens(user_id);
CREATE INDEX idx_refresh_expires ON refresh_tokens(expires_at);

CREATE TABLE company_assignments (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id TEXT NOT NULL,
    PRIMARY KEY (user_id, company_id)
);
CREATE INDEX idx_assignments_company ON company_assignments(company_id);

CREATE TABLE board_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT    NOT NULL,
    action       TEXT    NOT NULL,
    target       TEXT,
    payload_json TEXT,
    ts           TEXT    NOT NULL,
    ip           TEXT,
    user_agent   TEXT
);
CREATE INDEX idx_audit_user   ON board_audit(user_id);
CREATE INDEX idx_audit_action ON board_audit(action);
CREATE INDEX idx_audit_ts     ON board_audit(ts);

CREATE TABLE board_links (
    id              TEXT    PRIMARY KEY,
    from_company    TEXT    NOT NULL,
    to_company      TEXT    NOT NULL,
    relationship    TEXT    NOT NULL,
    scope_json      TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    requested_by    TEXT    NOT NULL,
    requested_at    TEXT    NOT NULL,
    decided_by      TEXT,
    decided_at      TEXT,
    note            TEXT,
    UNIQUE (from_company, to_company, status)
);
CREATE INDEX idx_links_from   ON board_links(from_company, status);
CREATE INDEX idx_links_to     ON board_links(to_company, status);
CREATE INDEX idx_links_status ON board_links(status);
