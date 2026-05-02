# Module 06 — Approval System

## Purpose

Queue + state machine for human-approved or security-approved actions. Hiring, termination approval, expenses, external actions, inter-company links, and security flags all pass through here. Emits a "blocked" signal for agents waiting on a pending approval.

## Responsibility boundaries

**Inside scope:**
- Approval types and routing (who approves)
- State machine: `pending → approved | denied | timeout | cancelled`
- Timeout job (different duration per type)
- Multi-step: some requests require more than one approver (e.g. inter-company link requires two board users)
- "blocked" signal for agents (read by the Orchestrator)
- Idempotency: the same `request_id` cannot be requested a second time

**Outside scope:**
- Computing who the approver is (route function per type — coordinated with Identity and Board API)
- The agent firing itself (Identity handles it; only calls the complete_fire callback)

## Dependencies

| Module | How |
|---|---|
| Storage (CompanyDB + BoardDB) | `approvals` table (per-company), `board_approvals` (board) |
| Event Store | `APPROVAL_REQUESTED`, `APPROVAL_DECIDED`, `APPROVAL_TIMEOUT` |
| Identity | for the fire complete callback on approval outcome |
| Clock | company-time or real-time for timeout? — real-time (timeout works even during pause) |

## Public API

```python
class Approvals:
    def __init__(self, db: CompanyDB, board_db: BoardDB,
                 events: EventStore): ...

    def request(self,
                kind: ApprovalKind,
                requester_id: str,
                payload: dict,
                route: ApprovalRoute,
                request_id: str | None = None,
                timeout_seconds: int | None = None) -> Approval:
        """Idempotent: if the same request_id already exists, returns the existing Approval.
        If timeout_seconds is None, the type's default is used."""

    def decide(self, request_id: str,
               decider_id: str,
               decision: Decision,
               note: str | None = None) -> Approval: ...

    def cancel(self, request_id: str, by: str, reason: str) -> Approval: ...

    def get(self, request_id: str) -> Approval: ...
    def pending_for(self, decider: ApprovalRoute) -> list[Approval]: ...

    def is_blocked(self, agent_id: str) -> bool:
        """Returns True if the agent has at least one pending approval."""

    def subscribe(self, callback: Callable[[Approval], None]) -> Handle: ...

    async def run_timeout_loop(self) -> None:
        """Background job — periodically moves expired approvals to timeout status."""

@dataclass(frozen=True)
class Approval:
    request_id: str
    company_id: str | None      # None → board-level
    kind: ApprovalKind
    requester_id: str           # agent_id or user_id
    payload: dict
    route: ApprovalRoute
    status: ApprovalStatus
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    expires_at: datetime
    created_at: datetime

class ApprovalKind(str, Enum):
    HIRE                = "hire"
    FIRE_DEPTH_1        = "fire_depth_1"
    EXPENSE             = "expense"
    EXTERNAL_ACTION     = "external_action"
    INTER_COMPANY_LINK  = "inter_company_link"
    SECURITY_FLAG       = "security_flag"
    BUDGET_INCREASE     = "budget_increase"

class ApprovalStatus(str, Enum):
    PENDING    = "pending"
    APPROVED   = "approved"
    DENIED     = "denied"
    TIMEOUT    = "timeout"
    CANCELLED  = "cancelled"

class Decision(str, Enum):
    APPROVE = "approve"
    DENY    = "deny"

@dataclass(frozen=True)
class ApprovalRoute:
    """Who can approve."""
    target: RouteTarget          # BOARD | AGENT(id) | SECURITY | DUAL(...)
    require_security: bool = False  # Whether Security's pre-veto is required
```

## Default timeout durations

| Kind | Timeout (real seconds) | Behaviour after timeout |
|---|---|---|
| HIRE | 7 days | Request lapses; HR reapplies |
| FIRE_DEPTH_1 | 7 days | Request lapses |
| EXPENSE | 24 hours | Request lapses |
| EXTERNAL_ACTION | 24 hours | Request lapses |
| INTER_COMPANY_LINK | 30 days | Request lapses |
| SECURITY_FLAG | 5 minutes | Auto-DENY (closed by default for security) |
| BUDGET_INCREASE | 7 days | Request lapses |

Can be overridden from configuration.

## Multi-step approval

There are scenarios such as `route.target = DUAL(BOARD, SECURITY)`:
- Some types require both Board and Security approval (e.g. high-amount EXPENSE)
- Implementation: sub-approvals under the main approval (sub_request); the main approval becomes APPROVED when all sub-approvals are APPROVED

Can be left out of scope for now — basic `target = BOARD | AGENT(id) | SECURITY`. Mark DUAL as Phase-2.

## Security pre-veto

If `route.require_security = True`:
1. The approval first goes to the Security agent (via sub-approval or sequentially)
2. If Security DENYs, the main approval is automatically DENIED (cannot be overridden — not even by Board)
3. If Security APPROVEs, the main decision is forwarded to route.target

This mechanism defaults to `True` for all `EXPENSE` and `EXTERNAL_ACTION` types.

## Persistence

**Per-company:**
```sql
CREATE TABLE approvals (
    request_id      TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    requester_id    TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    route_target    TEXT NOT NULL,        -- 'board' | 'agent:<id>' | 'security'
    require_security INTEGER NOT NULL DEFAULT 0,
    security_decision TEXT,                -- pending|approve|deny
    security_decided_at TEXT,
    status          TEXT NOT NULL,
    decided_by      TEXT,
    decided_at      TEXT,
    note            TEXT,
    expires_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_approvals_status   ON approvals(status);
CREATE INDEX idx_approvals_expires  ON approvals(expires_at);
```

**Board-level:**
```sql
CREATE TABLE board_approvals (
    request_id      TEXT PRIMARY KEY,
    company_id      TEXT,                  -- nullable for cross-company
    kind            TEXT NOT NULL,
    requester_user  TEXT,
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL,
    decided_by_user TEXT,
    decided_at      TEXT,
    note            TEXT,
    expires_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
```

## Idempotency

The caller provides `request_id` (e.g. a tool call UUID). A new `request` call with the same id:
- If the existing approval is pending: returns the same approval
- If approved/denied: returns the existing result (no new request is created)
- If timed out: returns the existing timeout; a new id is required for a new request

## Pending → blocked signal

`is_blocked(agent_id)`:
```sql
SELECT 1 FROM approvals
WHERE requester_id = ? AND status = 'pending'
LIMIT 1;
```

The Orchestrator can query this before running an agent; the full list of the agent's pending requests is retrieved via a tool (`list_my_approvals`).

## Event flow

- `request()` → `APPROVAL_REQUESTED` event
- `decide()` → `APPROVAL_DECIDED` event
- timeout → `APPROVAL_TIMEOUT` event
- Legacy fire approvals can still call `complete_fire` for old fire_depth_1
  requests; new fire actions do not require board approval.

## Test scenarios

1. `test_request_creates_pending`
2. `test_idempotent_same_request_id`
3. `test_decide_approve_marks_approved`
4. `test_decide_deny_marks_denied`
5. `test_decide_after_decision_idempotent` — re-submitting the same decision is a no-op
6. `test_decide_conflicting_decision_rejected`
7. `test_timeout_after_default_seconds`
8. `test_security_veto_blocks_board_approval`
9. `test_security_approve_lets_through_to_target`
10. `test_pending_for_route_correctly_filters`
11. `test_is_blocked_when_pending`
12. `test_event_emitted_on_each_state_change`
13. `test_cancel_pending`
14. `test_cancel_already_decided_rejected`

## Error classes

```python
class ApprovalError(Exception): ...
class ApprovalNotFound(ApprovalError): ...
class InvalidTransition(ApprovalError): ...
class UnauthorizedDecider(ApprovalError): ...
```

## Definition of Done

- [ ] All `ApprovalKind`s tested
- [ ] Security veto mechanism working
- [ ] Timeout job working (real-time based)
- [ ] Idempotency preserved
- [ ] Event emission complete
- [ ] Coverage ≥ 85%
- [ ] Identity integration (mock) tested

## File skeleton

```
modules/approvals/
├── PLAN.md
├── __init__.py
├── service.py
├── models.py
├── routing.py
├── timeout.py            # background loop
├── exceptions.py
├── migrations/
│   ├── company_001_init.sql
│   └── board_001_init.sql
└── tests/
    ├── test_request.py
    ├── test_decide.py
    ├── test_timeout.py
    ├── test_security_veto.py
    └── test_idempotency.py
```
