# Module 13 — Company Factory

## Purpose

Company bootstrap. The Board says "new company"; the Factory brings up all modules in sequence: DB, workspaces, CEO+HR+Security agents, Clock, Orchestrator. Clean shutdown and archive when the company is closed.

## Responsibility boundaries

**Inside:**
- Company registration (in the board DB)
- Per-company DB creation + running migrations for all modules
- Workspace directories, default quota
- Loading the default pricing catalogue
- Creating bootstrap agents (CEO + HR + Security)
- Optional initial employees (if specified in the board spec)
- Starting Clock + Orchestrator + all modules
- "Welcome" message to CEO: "Company founded, your mission: …"
- Company shutdown: Clock pause → wait for idle → stop all agents → archive DB
- Restarting the company (on process restart if not closed)

**Outside:**
- The individual behaviour of each module (each handles its own)
- Board UI

## Dependencies

All modules. Factory orchestrates them.

## Public API

```python
class CompanyFactory:
    def __init__(self, board_db: BoardDB, root: Path, redis: Redis,
                 module_registry: ModuleRegistry): ...

    async def create_company(self, spec: CompanySpec,
                             requested_by_user: str) -> CompanyHandle: ...

    async def close_company(self, company_id: str,
                            requested_by_user: str) -> None: ...

    async def restart_company(self, company_id: str) -> CompanyHandle:
        """Opens an existing company after a process restart."""

    async def list_active(self) -> list[CompanySummary]: ...

    def get_handle(self, company_id: str) -> CompanyHandle: ...

@dataclass(frozen=True)
class CompanySpec:
    name: str
    mission: str
    industry: str | None
    initial_budget_usd: float
    company_disk_quota_mb: int
    default_agent_quota_mb: int
    extra_agents: list[ExtraAgentSpec] = field(default_factory=list)
    auto_approve_threshold_usd: float = 0.0   # default: 0 → everything requires approval
    clock_rate: ClockRate = ClockRate.realtime()

@dataclass(frozen=True)
class ExtraAgentSpec:
    role: Role
    persona_ref: str
    reports_to_role: Role  # CEO, HR, etc. — instance ID resolved at bootstrap

@dataclass(frozen=True)
class CompanyHandle:
    company_id: str
    db: CompanyDB
    workspace: Workspace
    quota: Quota
    events: EventStore
    clock: Clock
    identity: Org
    cost: Pricing
    budget: Budget
    subscriptions: Subscriptions
    approvals: Approvals
    memory: Memory
    tools: Tools
    connector: Connector
    orchestrator: Orchestrator
    agents: AgentDirectory
    security_policy: SecurityPolicy

    async def shutdown(self): ...
```

## Bootstrap flow (critical)

```python
async def create_company(spec, user_id):
    company_id = generate_ulid()

    # 1. Register in board DB
    board_db.execute("INSERT INTO companies ...")

    # 2. Per-company storage
    company_db = CompanyDB.init(company_id, root)
    company_db.migrate()  # runs migrations for all modules
    workspace = Workspace(company_id, root, identity_proxy, events_proxy)
    quota = Quota(company_db)
    quota.set_limit(Company(company_id), spec.company_disk_quota_mb)

    # 3. Event store
    events = EventStore(company_db)
    events.append(COMPANY_CREATED, {"spec": spec.dict(), "by": user_id})

    # 4. Clock (don't start yet)
    clock = Clock(company_id, company_db, events,
                  idle_probe=lambda: orchestrator.is_company_idle(),
                  rate=spec.clock_rate)

    # 5. Identity
    approvals = Approvals(company_db, board_db, events)
    identity = Org(company_db, events, approvals_requester(approvals))

    # 6. Cost
    pricing = Pricing(company_db)
    pricing.load_default_catalog()
    budget = Budget(company_db, events, pricing)
    budget.set_total(spec.initial_budget_usd)
    subscriptions = Subscriptions(company_db, budget, events)

    # 7. Memory
    memory = Memory(company_db, identity, embedder)

    # 8. Connector
    connector = Connector(...)
    connector.set_auto_approve_threshold(spec.auto_approve_threshold_usd)

    # 9. Tools
    tools = Tools(identity, budget, approvals, workspace, memory,
                  events, connector)
    tools.register_builtins()

    # 10. Bootstrap agents (HR check bypassed — bootstrap=True)
    ceo = identity.add_agent(role=CEO, persona_ref="default",
                             reports_to=None, requested_by=user_id,
                             via_hr=False, bootstrap=True)
    hr  = identity.add_agent(role=HR, persona_ref="default",
                             reports_to=ceo.id, ..., bootstrap=True)
    sec = identity.add_agent(role=SECURITY, persona_ref="default",
                             reports_to=None,           # Security reports to Board
                             ..., bootstrap=True)

    # Assign quota
    for a in [ceo, hr, sec]:
        workspace.create_for_agent(a.id, spec.default_agent_quota_mb)

    # 11. Optional extra agents
    for ex in spec.extra_agents:
        manager_id = resolve_role_to_id(ex.reports_to_role)
        a = identity.add_agent(role=ex.role, persona_ref=ex.persona_ref,
                               reports_to=manager_id, bootstrap=True, ...)
        workspace.create_for_agent(a.id, spec.default_agent_quota_mb)

    # 12. Agent runtime + Orchestrator
    agents = AgentDirectory(...)
    orchestrator = Orchestrator(company_id, redis, identity, agents, events, clock)

    for a in identity.all(status=ACTIVE):
        agents.add(Agent(a.id, deps=AgentDeps(...)))

    await orchestrator.start()
    for a in agents.all():
        await a.start()

    # 13. Security policy
    security_policy = SecurityPolicy(sec.id, deps)

    # 14. Clock start
    await clock.start()

    # 15. Welcome message → CEO
    await orchestrator.system_send(
        to_agent=ceo.id,
        kind=USER_REQUEST,
        content=f"Company founded. Mission: {spec.mission}\n"
                f"Budget: ${spec.initial_budget_usd}\n"
                f"Your team: HR ({hr.id}), Security ({sec.id}). "
                f"Set your initial goals and make a plan."
    )

    return CompanyHandle(...)
```

## Shutdown flow

```python
async def close_company(company_id, user_id):
    handle = self.get_handle(company_id)

    events.append(COMPANY_CLOSED, {"by": user_id, "stage": "starting"})

    # 1. Clock pause (soft)
    await handle.clock.pause(requested_by=user_id)

    # 2. Wait until idle
    await wait_until(handle.orchestrator.is_company_idle, timeout=300)

    # 3. Stop agents
    for a in handle.agents.all():
        await a.stop(drain=True)

    # 4. Orchestrator stop
    await handle.orchestrator.stop()

    # 5. Clock stop
    await handle.clock.stop()

    # 6. Archive DB
    handle.db.archive(dest=root / "archive" / company_id)
    workspace.archive(company_id)

    # 7. Update board record
    board_db.execute("UPDATE companies SET status='closed' ...")
    events.append(COMPANY_CLOSED, {"stage": "complete"})
```

## Restart flow (after process restart)

Brings up all open companies:
```python
async def restart_all_active():
    for company in board_db.query("SELECT * FROM companies WHERE status='active'"):
        await self.restart_company(company.id)
```

`restart_company` sets up the same components as `create_company` but:
- Does not create new agents; retrieves existing ones from DB
- Does not send the welcome message
- Resumes Clock from its saved state

## Persistence

Board DB:
```sql
CREATE TABLE companies (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    mission       TEXT,
    industry      TEXT,
    spec_json     TEXT NOT NULL,
    status        TEXT NOT NULL,        -- creating | active | closing | closed
    created_at    TEXT NOT NULL,
    closed_at     TEXT,
    created_by    TEXT NOT NULL          -- user id
);
CREATE INDEX idx_companies_status ON companies(status);
```

## Edge cases

| Scenario | Behaviour |
|---|---|
| create_company fails mid-way | All artifacts rolled back (delete DB, delete directory); status `failed` |
| Two companies with same name | Allowed — IDs differ |
| Board user unauthorised | Board API layer checks; Factory fulfils the call assuming user_id is trusted |
| Bootstrap personas missing | Default persona files are checked; error if missing |
| Disk insufficient (on host) | OSError → rollback |
| Process crash during bootstrap | On restart, if status is `creating`: cleanup or resume — cleanup preferred for now |
| Idle timeout on close (timeout 300s) | Force stop + warning event |
| Restarting but Redis is down | Company in `degraded` state; orchestrator stays offline, Board notified |

## Test scenarios

1. `test_create_company_creates_db_workspaces_agents`
2. `test_create_company_emits_company_created_event`
3. `test_create_company_with_extra_agents_links_correct_managers`
4. `test_create_company_failure_rolls_back`
5. `test_close_company_pauses_then_drains`
6. `test_close_company_archives_db_and_workspaces`
7. `test_close_company_force_after_timeout`
8. `test_restart_company_resumes_with_existing_agents`
9. `test_restart_does_not_send_welcome`
10. `test_concurrent_create_companies_isolated`
11. `test_initial_budget_set_correctly`
12. `test_clock_starts_running_after_create`

## Error classes

```python
class FactoryError(Exception): ...
class BootstrapFailed(FactoryError): ...
class CompanyAlreadyExists(FactoryError): ...
class CompanyNotFound(FactoryError): ...
class ShutdownTimeout(FactoryError): ...
```

## Definition of Done

- [ ] create_company end-to-end tested (integration test)
- [ ] close_company graceful + force fallback
- [ ] restart_company preserves state
- [ ] Failure rollback tested
- [ ] Default persona and pricing files in place
- [ ] Coverage ≥ 80% (integration-heavy)
