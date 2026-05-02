# Module 14 — Board Backend API

## Purpose

HTTP/SSE interface for the Board UI. Multi-user JWT auth. Company management, approval queues, budget settings, live feed, replay. RBAC (admin, observer, etc.).

## Responsibility boundaries

**Inside:**
- FastAPI application
- JWT auth: register, login, refresh
- User CRUD (for admin)
- Company endpoints (proxy to Factory)
- Approval inbox (cross-company aggregation)
- Budget / disk quota / allowlist settings
- Inter-company link approvals (proxy)
- Live feed: SSE per company or global
- Replay endpoint: filtered event reading
- Audit log (who did what on the Board)

**Outside:**
- Frontend (Board UI is a separate module)
- Company-internal behaviour

## Dependencies

| Module | How |
|---|---|
| Factory | company CRUD |
| Approvals | board-level + per-company aggregation |
| Event Store | SSE stream |
| Identity (per company) | proxy queries |
| Cost (per company) | budget read/write |
| Inter-Company | link approvals |

## Public API (HTTP endpoints)

### Auth
- `POST /auth/register` — new user (first user becomes admin)
- `POST /auth/login` → JWT (access + refresh)
- `POST /auth/refresh` → new access token
- `POST /auth/logout`
- `GET  /auth/me`

### Users (admin only)
- `GET  /users`
- `POST /users` — admin adds other admins/observers
- `PATCH /users/{id}` — change role
- `DELETE /users/{id}` — soft delete

### Companies
- `POST /companies` — `CompanySpec` body, Factory.create_company
- `GET  /companies` — companies visible to the user
- `GET  /companies/{id}`
- `POST /companies/{id}/pause`
- `POST /companies/{id}/resume`
- `POST /companies/{id}/close`
- `PATCH /companies/{id}` — budget, disk quota, allowlist update

### Approvals
- `GET  /approvals/pending` — items the user can approve (route calculation)
- `GET  /approvals/{id}`
- `POST /approvals/{id}/decide` — `{decision, note}`

### Settings (per company)
- `GET  /companies/{id}/budget`
- `PATCH /companies/{id}/budget`
- `GET  /companies/{id}/allowlist`
- `PATCH /companies/{id}/allowlist`
- `GET  /companies/{id}/auto_approve_thresholds`
- `PATCH /companies/{id}/auto_approve_thresholds`

### Org chart & expenses
- `GET /companies/{id}/agents`
- `GET /companies/{id}/agents/{agent_id}/workspace` — manager or admin only
- `GET /companies/{id}/expenses`
- `GET /companies/{id}/budget/state`

### Inter-company
- `POST /links` — new link request
- `GET  /links`
- `POST /links/{id}/decide`

### Live feed (SSE)
- `GET /companies/{id}/stream` (SSE) — `?since=<event_id>` optional
- `GET /stream` — aggregate all companies (admin)

### Replay
- `GET /companies/{id}/events?since=&until=&kinds=&actor=`

### Audit
- `GET /audit` — board action log

## Auth details

JWT:
- Access token: 15 min
- Refresh token: 30 days
- Algorithm: HS256 (configurable HMAC secret)
- Claims: `sub` (user_id), `role`, `iat`, `exp`

```python
class UserRole(str, Enum):
    ADMIN     = "admin"      # everything
    OPERATOR  = "operator"   # manage companies, decide approvals
    OBSERVER  = "observer"   # read-only
```

First registration is automatically admin. Subsequent registrations default to observer (admin promotes to operator).

## RBAC matrix

| Endpoint | Admin | Operator | Observer |
|---|---|---|---|
| Companies CRUD | ✓ | ✓ (assigned to them) | ✗ |
| Companies read | ✓ | ✓ | ✓ |
| Approvals decide | ✓ | ✓ | ✗ |
| Settings change | ✓ | ✓ | ✗ |
| Users CRUD | ✓ | ✗ | ✗ |
| Live feed | ✓ | ✓ | ✓ |
| Replay | ✓ | ✓ | ✓ |
| Workspace read (any agent) | ✓ | ✓ (own companies) | ✗ |

`operator` "own companies" → `companies.created_by` or explicit assignment table.

## SSE implementation

```python
@router.get("/companies/{cid}/stream")
async def stream(cid: str, since: int = 0, user = Depends(auth)):
    require_company_access(user, cid)
    handle = factory.get_handle(cid)
    async def gen():
        async for ev in handle.events.stream(since=since):
            yield f"data: {ev.to_json()}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

Backpressure: client disconnect detection via `await request.is_disconnected()`. Heartbeat ping every 15 sec.

## Audit log

When a board user performs an action:
```python
audit.log(user_id, action, target, payload)
```

Table:
```sql
CREATE TABLE board_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    action      TEXT NOT NULL,        -- approval.decide, company.close, ...
    target      TEXT,                  -- request_id, company_id, ...
    payload_json TEXT,
    ts          TEXT NOT NULL,
    ip          TEXT,
    user_agent  TEXT
);
```

## Persistence (additional board DB tables)

```sql
CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,        -- bcrypt
    role            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    last_login      TEXT,
    deleted         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE refresh_tokens (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    token_hash  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE company_assignments (
    user_id     TEXT NOT NULL,
    company_id  TEXT NOT NULL,
    PRIMARY KEY (user_id, company_id)
);

CREATE TABLE board_audit (...);  -- see above
```

## Error handling

FastAPI exception handler maps all `*Error`s to HTTP:
- `ApprovalNotFound` → 404
- `OverBudget` → 409
- `Unauthorized` → 401
- `Forbidden` → 403
- `ValidationError` (pydantic) → 422

All error responses use the format `{"error": "...", "code": "...", "details": {...}}`.

## Test scenarios

1. `test_register_first_user_is_admin`
2. `test_login_returns_jwt`
3. `test_jwt_expired_rejected`
4. `test_refresh_token_rotates`
5. `test_observer_cannot_create_company`
6. `test_operator_decides_only_own_company_approvals`
7. `test_create_company_calls_factory`
8. `test_pause_resume_endpoints`
9. `test_close_company_endpoint`
10. `test_pending_approvals_route_correctly`
11. `test_decide_approval_persists_and_audits`
12. `test_settings_update_propagates_to_factory_handle`
13. `test_sse_stream_emits_events_in_order`
14. `test_sse_disconnect_cleanup`
15. `test_replay_filter_by_kind`
16. `test_audit_log_captures_actions`
17. `test_workspace_read_observer_denied`

## Definition of Done

- [ ] All endpoints implemented
- [ ] JWT auth + RBAC tested
- [ ] OpenAPI schema auto-generated
- [ ] SSE stream handles 100+ events/sec
- [ ] Audit log populated
- [ ] Coverage ≥ 80%

## File skeleton

```
modules/board_api/
├── PLAN.md
├── __init__.py
├── app.py                 # FastAPI app
├── auth/
│   ├── jwt.py
│   ├── routes.py
│   └── deps.py
├── routes/
│   ├── companies.py
│   ├── approvals.py
│   ├── settings.py
│   ├── stream.py
│   ├── replay.py
│   ├── links.py
│   └── users.py
├── audit.py
├── rbac.py
├── exceptions.py
├── migrations/board_002_users.sql
└── tests/
    ├── test_auth.py
    ├── test_companies.py
    ├── test_approvals.py
    ├── test_settings.py
    ├── test_sse.py
    └── test_rbac.py
```
