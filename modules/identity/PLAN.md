# Module 04 — Identity & Org Chart

## Purpose

Agent identity, reporting chain, and **hire/fire permission matrix**. The single source of authority for workspace read permissions. Protects special roles: CEO, HR, and Security.

## Mechanical summary of your rules

- **Hiring** always goes through HR (HR executes it; CEO or a manager initiates the request)
- **Firing** always goes through HR
- If manager X wants to fire target Y: depth = `depth(X→Y)`
  - `depth == 1` → approval from one level above (X's manager) is **required**
  - `depth >= 2` → fire without approval, but notify Y's direct manager
- Upper constraint: no one can fire someone above or laterally
- CEO cannot be directly fired (the Board closes the company)
- HR and Security are special roles — a replacement must be appointed immediately upon firing (by Factory or CEO hiring a new HR/Security)

## Responsibility boundaries

**In scope:**
- Agent CRUD (create, read, fire — firing is soft: `status='fired'`, record is preserved)
- Org graph (parent_of, manager_of, direct_reports, descendants)
- Fire authorization checks and flow (routing to Approvals when required)
- HR routing: all hire/fire operations require the `via_hr` parameter
- Evaluating workspace read permissions (`can_read_workspace`)
- Protection of bootstrap roles (CEO, HR, Security)

**Out of scope:**
- Human user Board login (Board API's responsibility)
- Persona prompt details (held by Agent Runtime)
- The approval queue itself (Approvals module)

## Dependencies

| Module | How |
|---|---|
| Storage (CompanyDB) | `agents` table |
| Event Store | hire/fire/notify events |
| Approvals | create request for approval-required fires (Protocol injected) |

## Public API

```python
class Org:
    def __init__(self, db: CompanyDB, events: EventStore,
                 approvals: ApprovalRequester): ...

    # CRUD
    def add_agent(self, *, role: Role, persona_ref: str,
                  reports_to: str | None, requested_by: str,
                  via_hr: bool) -> Agent:
        """add_agent always expects via_hr=True (Factory bootstrap exception
        — Factory calls with bootstrap=True)."""

    def get(self, agent_id: str) -> Agent: ...
    def all(self, status: Status | None = None) -> list[Agent]: ...

    # Org graph
    def manager_of(self, agent_id: str) -> str | None: ...
    def direct_reports(self, agent_id: str) -> list[str]: ...
    def all_descendants(self, agent_id: str) -> list[str]: ...
    def depth(self, ancestor_id: str, descendant_id: str) -> int | None:
        """Positive int for how many levels below; if not None, is an ancestor.
        None if not an ancestor at all."""
    def chain_up(self, agent_id: str) -> list[str]: ...

    # Hire/Fire
    def fire(self, *, actor_id: str, target_id: str,
             reason: str, via_hr: bool) -> FireResult:
        """Rejected if via_hr=False.
        Result:
          - DIRECT_FIRED: fired immediately, notify sent
          - PENDING_APPROVAL: legacy queued approval path
          - DENIED: unauthorized or violates rules
        """

    def complete_fire(self, request_id: str, decision: ApprovalDecision) -> None:
        """Legacy approvals callback for pre-direct-fire queued requests."""

    # Workspace permissions (called by Storage)
    def can_read_workspace(self, reader_id: str, owner_id: str) -> bool:
        """True iff reader == owner OR reader == manager_of(owner)."""

    # Special role guards
    def is_special(self, agent_id: str) -> bool:  # CEO/HR/Security
    def has_role(self, role: Role, status: Status = Status.ACTIVE) -> bool:

@dataclass(frozen=True)
class Agent:
    id: str
    role: Role
    persona_ref: str       # prompt key on the Agent Runtime side
    reports_to: str | None
    status: Status
    hired_at: datetime
    fired_at: datetime | None

class Role(str, Enum):
    CEO = "ceo"
    CFO = "cfo"
    CTO = "cto"
    HR = "hr"
    SECURITY = "security"
    ENGINEER = "engineer"
    DESIGNER = "designer"
    MARKETER = "marketer"
    SALES = "sales"
    OTHER = "other"

class Status(str, Enum):
    ACTIVE = "active"
    FIRED = "fired"

class FireResult(str, Enum):
    DIRECT_FIRED = "direct_fired"
    PENDING_APPROVAL = "pending_approval"
    DENIED = "denied"
```

## Fire algorithm (critical)

```
def fire(actor, target, reason, via_hr):
    if not via_hr: return DENIED("must_route_via_hr")
    if actor.role not in firing_capable_roles: return DENIED("not_authorized")
    if target.id == actor.id: return DENIED("self_fire")
    if target.role == CEO: return DENIED("ceo_cannot_be_fired_directly")

    d = depth(actor, target)
    if d is None or d <= 0: return DENIED("not_subordinate")

    # Any subordinate can be fired without board approval.
    # If this is an indirect report, notify their direct manager.
    return _do_fire(...)
    _do_fire(target)
    notify_direct = manager_of(target)  # still unchanged before the fire
    events.append(AGENT_FIRED, {...})
    if notify_direct and notify_direct != actor.id:
        events.append("agent.fire_notify", {
            "to": notify_direct,
            "fired_agent": target.id,
            "actor": actor.id,
            "reason": reason,
        })
    return DIRECT_FIRED
```

**Important detail:** In the `depth == 1` scenario, if the actor has no manager above them (e.g. CEO) → the approval is routed to the Board. Firing the CEO's direct report (e.g. CFO) requires Board approval. This keeps the system consistent — there is always "one level above"; the level above the top is the Board.

## Hire flow

`add_agent(via_hr=True)` is always assumed to be HR-approved. The HR decision has already been recorded in the Approval System; Org only stores the record. Factory bootstrap exception: the `bootstrap=True` parameter (used only by Factory) bypasses the HR check.

## Persistence

```sql
CREATE TABLE agents (
    id          TEXT PRIMARY KEY,           -- ulid
    role        TEXT NOT NULL,
    persona_ref TEXT NOT NULL,
    reports_to  TEXT REFERENCES agents(id),
    status      TEXT NOT NULL,
    hired_at    TEXT NOT NULL,
    fired_at    TEXT,
    fired_by    TEXT REFERENCES agents(id),
    fire_reason TEXT
);
CREATE INDEX idx_agents_reports_to ON agents(reports_to);
CREATE INDEX idx_agents_role       ON agents(role);
CREATE INDEX idx_agents_status     ON agents(status);

CREATE TABLE fire_pending (
    request_id   TEXT PRIMARY KEY,
    actor_id     TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    reason       TEXT NOT NULL,
    decider_id   TEXT NOT NULL,             -- either agent_id or 'BOARD'
    created_at   TEXT NOT NULL
);
```

## Edge cases

| Scenario | Behavior |
|---|---|
| Who can fire HR? | Any manager in the chain, but the last active HR is protected until a replacement exists |
| Who can fire Security? | Any manager in the chain; no board approval |
| CEO wants to fire their direct report (depth=1) | Direct fire; no board approval |
| Manager A, target B; B's manager is C, A's manager is C (could be siblings? no, it's a tree) | Tree structure is guaranteed; sibling fire is not possible |
| Second fire request for the same target | Denied because the target is no longer active |
| Approval is rejected | `complete_fire` is called with `DENIED` → `fire_pending` is removed, target remains active |
| Subordinates of the target during fire | Subordinates become orphaned; `reports_to` is set to null and an "orphan" event is emitted → signal to CEO/HR to reassign |
| Cycle (A→B, B→A) | Rejected during insert (`add_agent` parent check) |

## Special role guards

```
If remove_role_atomically(role) is called, the company becomes unstable. Rules:
- CEO: can never be fired through this API
- HR: at least one active HR must exist; firing cannot complete until a new HR is active
   → waits in `pending_replacement` state until Factory or CEO adds a new HR
- Security: same as HR

These invariants are guarded in Org.fire.
```

## Test scenarios

1. `test_add_agent_requires_via_hr`
2. `test_bootstrap_can_skip_via_hr`
3. `test_fire_self_denied`
4. `test_fire_ceo_denied`
5. `test_fire_depth_1_fires_directly_without_approval`
6. `test_fire_depth_2_immediate_with_notify`
7. `test_fire_depth_3_immediate_with_notify`
8. `test_ceo_fires_direct_report_without_board_approval`
9. `test_fire_unrelated_agent_denied`
10. `test_second_fire_call_sees_target_inactive`
11. `test_orphan_event_emitted_when_manager_fired`
12. `test_can_read_workspace_self_true`
13. `test_can_read_workspace_manager_true`
14. `test_can_read_workspace_grandmanager_false`
15. `test_can_read_workspace_unrelated_false`
16. `test_hr_fire_blocked_until_replacement`
17. `test_security_can_be_fired_by_manager_without_board_approval`
18. `test_cycle_insert_rejected`
19. `test_last_remaining_hr_stays_protected_after_direct_fire`

## Error classes

```python
class IdentityError(Exception): ...
class FireDenied(IdentityError): ...
class HireDenied(IdentityError): ...
class CycleDetected(IdentityError): ...
class SpecialRoleProtected(IdentityError): ...
```

## Definition of Done

- [ ] All fire scenarios (depth 1, 2, 3+) tested and behaving correctly
- [ ] Workspace read permissions return correct answers to Storage
- [ ] Special role protection is working (CEO and HR replacement)
- [ ] Orphan handling events are emitted
- [ ] Cycle prevention is tested
- [ ] Approval Protocol integration (with mock) is tested
- [ ] Coverage ≥ 90% (critical module)
- [ ] mypy strict + ruff clean

## File skeleton

```
modules/identity/
├── PLAN.md
├── __init__.py
├── org.py
├── models.py            # Agent, Role, Status, FireResult
├── policy.py            # fire rules, depth math
├── protocols.py         # ApprovalRequester
├── exceptions.py
├── migrations/001_init.sql
└── tests/
    ├── test_crud.py
    ├── test_fire_depth_1.py
    ├── test_fire_deeper.py
    ├── test_special_roles.py
    ├── test_workspace_perm.py
    └── test_orphan.py
```
