# Module 17 — Integration & E2E

## Purpose

The "stitch" phase after all modules are complete. Connecting real cross-module implementations (removing mocks), integration tests, scenario tests, bug fixes, and performance tuning.

This module is **not a code module** — integration code already lives in Factory. It only contains **integration tests**, **CLI helpers**, **deploy scripts**, and **documentation**.

## Responsibility boundaries

**In scope:**
- E2E tests (real Redis, real SQLite, mock LLM)
- Scenario tests — long-running agent interactions
- Performance/load tests (10 companies, 50 agents, 1 hour)
- CLI: `acf` command (start, create-company, list, pause, ...)
- Deploy skeleton (Docker Compose: redis + main process + UI)
- Migration runner (runs all modules' migrations in order)
- README, developer documentation, architecture diagrams
- Health check endpoint (Board API)

**Out of scope:**
- Adding new features (those belong in their respective modules)

## Dependencies

All modules.

## Integration test suite

```
tests/e2e/
├── conftest.py                  # real redis container (testcontainers)
├── test_create_first_message.py
├── test_hire_flow.py
├── test_fire_with_approval.py
├── test_external_call_flow.py
├── test_pause_resume.py
├── test_loop_detection.py
├── test_inter_company.py
├── test_security_veto.py
├── test_replay.py
├── test_close_company.py
└── scenarios/
    ├── test_scenario_marketing_campaign.py
    ├── test_scenario_build_website.py
    └── test_scenario_vendor_relationship.py
```

### Key scenarios

**Scenario 1 — Company creation + first message**
1. Board user register + login
2. POST /companies (CompanySpec)
3. Factory bootstrap — CEO + HR + Security agent created
4. Welcome message sent to CEO
5. Visible in live feed (SSE assertion)
6. Mock LLM returns "hello team, let's get started" for CEO
7. EVENT_SENT, AGENT_HEARTBEAT events in event store in correct order

**Scenario 2 — Hiring flow**
1. CEO tells HR "hire two engineers" (mock LLM scripted response)
2. HR calls `propose_hire` tool → Approval (board)
3. Test client `decide approve`
4. HR calls `add_agent` → new engineer agent created + workspace
5. Engineer becomes CEO's direct report (chain: CEO → engineer)
6. Engineer receives first message

**Scenario 3 — Firing (depth=1, depth=2)**
1. CEO wants to fire a new engineer (depth=1)
2. Approval sent to Board (CEO's superior = Board)
3. Reject → engineer stays active
4. Approve → fire
5. After approval: workspace deleted, status fired event written
6. CEO fires a deeper intern (depth=2) → no approval + manager notify

**Scenario 4 — External call + approval**
1. Engineer says "rent a server" → Connector → Approval
2. Security pre-veto: rule check → APPROVE
3. Board approves
4. Mock hosting service "droplet created"
5. Expense deducted, recorded in Event Store
6. Budget state updated

**Scenario 5 — Pause/Resume**
1. Running agent with in-flight tool call
2. Board calls pause
3. In-flight completes (mock LLM returns async)
4. New messages rejected
5. State paused
6. Resume → picks up from queue and continues

**Scenario 6 — Loop detection**
1. Mock LLM makes two agents talk with the same message repeatedly
2. Orchestrator alarms at 5 repetitions
3. Agent suspended after 3 alarms
4. Manager notified

**Scenario 7 — Replay**
1. Produce 100 events
2. Call replay endpoint with `until=50`
3. Events returned in order
4. UI mock re-render test (e2e side)

**Scenario 8 — Inter-company vendor**
1. Company A and B exist
2. A → B link request, board approves
3. A's CEO tells B "get a price quote" → cross-message
4. B's HR (default inbox) receives it, forwards to CTO
5. Quote returned from B

**Scenario 9 — Security critical flag**
1. Mock LLM has engineer attempt a request to an out-of-allowlist domain
2. Static rule CRITICAL → automatic DENY + agent suspend
3. SECURITY_FLAG approval pending on Board
4. Board rejects or takes custom action

**Scenario 10 — Close**
1. Active company with tool-using agents
2. Board calls close
3. Pause → waits for idle → agent stop → DB archive
4. COMPANY_CLOSED written to event store
5. If restarted, company is invisible (status closed)

## Performance / load tests

```
tests/load/
├── test_load_10_companies_50_agents.py    # 1 hour simulated, accelerated clock
├── test_load_sse_50_clients.py
└── test_load_event_store_writes.py        # 10k events/sec
```

Targets:
- 10 companies × 5 agents concurrently, accelerated clock (1s/day) completing a 30-day month
- 50 SSE clients watching a single company with no drops
- Event store sustaining 1k events/sec

## CLI

```
acf init                    # prepare data/ directory, board DB migrate
acf serve                   # start API + factory
acf company list
acf company create --spec=spec.yaml
acf company pause <id>
acf company resume <id>
acf company close <id>
acf company describe <id>
acf user create --email=<> --role=admin
acf approvals pending
acf events tail <company_id>
acf events replay <company_id> --until=<event_id>
```

`acf` Python entry point (Click or Typer).

## Deploy skeleton

```
deploy/
├── docker-compose.yml          # redis + acf-api + acf-ui + nginx
├── Dockerfile.api
├── Dockerfile.ui
├── nginx.conf
├── .env.example
└── README.md
```

Not production-grade — single-host for easy developer onboarding. Production deploy after the concept phase.

## Migration runner

```bash
acf db migrate
```

Collects all board and company migrations from every module (`modules/*/migrations/`) and applies them incrementally using a `schema_version` table.

## Documentation

```
docs/
├── architecture.md             # architecture diagram + flows
├── modules.md                  # module-by-module summary
├── api.md                      # generated from OpenAPI
├── personas.md                 # description of default agent personas
├── operations.md               # CLI, restart, troubleshoot
└── extending.md                # how to add a new tool or service
```

## Definition of Done (project total)

- [ ] All 16 modules passed their own DoDs
- [ ] All 10 scenario tests pass
- [ ] Load tests meet the target numbers
- [ ] CLI commands working
- [ ] Starts locally with Docker Compose
- [ ] README + docs/ complete
- [ ] One end-to-end demo: "Board user register → open company → CEO+HR+Security up → first message → hire approval → engineer hired → external action → close" passes in a single run

## File skeleton

```
modules/integration/
├── PLAN.md
├── cli.py                  # acf entry point
├── migrations.py           # runner
├── healthcheck.py
├── tests/
│   ├── e2e/                # see tree above
│   └── load/
└── ...
deploy/
└── ... (see above)
docs/
└── ...
```

## Open questions (to be resolved during integration)

- LLM provider key rotation
- Production hardening (TLS, secret manager, rate limit at gateway)
- Multi-board (multi-tenant)
- Backup/restore policy — is an `acf backup` command needed?
- Retention: event store 6 months, then what? Soft delete + archive?
- Observability: Prometheus metrics export?

These questions are out of scope for the main plan; to be discussed with the user during the integration phase.
