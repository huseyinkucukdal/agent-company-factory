-- Storage module: per-company schema (version 1).
-- Notes:
--   * `schema_version` is created by the migration runner, not here.
--   * Other modules will add their own migrations under their own module key.

CREATE TABLE workspaces (
    agent_id    TEXT    PRIMARY KEY,
    quota_bytes INTEGER NOT NULL,
    used_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE TABLE company_quota (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    quota_bytes INTEGER NOT NULL DEFAULT 0
);

INSERT INTO company_quota (id, quota_bytes) VALUES (1, 0);
