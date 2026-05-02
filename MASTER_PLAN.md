# Agent Company Factory — Master Plan

## 1. Vision

A platform for creating and managing autonomous AI companies that can do real-world work, operated from a **Board** interface. Each company runs with an isolated DB, an isolated filesystem quota, and its own agent team. Companies write code, rent servers, run social media ads, send emails — every action that affects the outside world goes through Board approval. Everything is observable live from the Board: who did what, what was discussed, what was spent. Fully replayable.

Companies are not simulations. The CEO and team complete real tasks. A human-approved management layer (Board, Security, Manager approvals) provides both a security and ethical control point.

## 2. Core decisions (fixed, immutable starting points)

| Topic | Decision |
|---|---|
| Backend language | Python 3.11+ |
| Web framework | FastAPI (REST + SSE) |
| Agent runtime | Claude Agent SDK |
| Message queue | Redis Streams (intra-company + Orchestrator) |
| DB | SQLite per company + SQLite for board-level |
| Vector store | sqlite-vss (embedded in DB, portable) |
| Auth | JWT, multi-user |
| Frontend | Next.js + Tailwind, SSE for live updates |
| Time | 1 real hour = 1 company day |
| Bootstrap roles | CEO + HR + Security (mandatory) |
| Hiring | Always routed through HR |
| Firing | Always routed through HR, hierarchical approval rules (see Identity module) |
| Direct Board access | HR and Security agents only |
| Financial authority | CFO (if present); even small expenses require Board approval |
| Cost scope | LLM tokens + tool fees (per-call, subscriptions, domain, server, ads) |
| Pause behaviour | No new messages accepted; in-flight tasks complete |
| Inter-company communication | Board-approved only (vendor/customer relationships supported) |
| CEO transfer | Not allowed — if the company closes, the CEO is also closed |
| Runtime | **The entire application runs inside Docker containers.** Dev and prod are identical. No Python installation assumed on the host; all commands go through `docker compose`. |

## 3. Development approach: horizontal module-by-module

This project progresses **horizontally**: finish one module with all its features → pass its unit tests → move to the next. The vertical/MVP approach is **not used** because it produces throwaway code.

Each module has its own `PLAN.md`, its own tests, and its own public API. Real implementations within a module depend on other modules through `Protocol`/`abc.ABC` interfaces — developed with mocks until the integration phase.

## 4. Module list (dependency order)

| # | Module | Folder | Depends on |
|---|---|---|---|
| 1 | Storage Layer | `modules/storage/` | — |
| 2 | Event Store | `modules/event_store/` | Storage |
| 3 | Clock | `modules/clock/` | Storage, Event Store |
| 4 | Identity & Org Chart | `modules/identity/` | Storage, Event Store |
| 5 | Cost & Budget Engine | `modules/cost/` | Storage, Event Store, Clock |
| 6 | Approval System | `modules/approvals/` | Storage, Event Store, Identity |
| 7 | Memory Subsystem | `modules/memory/` | Storage |
| 8 | Tool Layer | `modules/tools/` | Identity, Cost, Approvals, Storage, Memory |
| 9 | Connector | `modules/connector/` | Cost, Approvals, Tools |
| 10 | Agent Runtime | `modules/agent_runtime/` | Identity, Memory, Tools, Event Store |
| 11 | Orchestrator | `modules/orchestrator/` | Agent Runtime, Event Store, Clock, Identity |
| 12 | Security Agent | `modules/security_agent/` | Agent Runtime, Approvals, Connector |
| 13 | Company Factory | `modules/factory/` | All |
| 14 | Board Backend API | `modules/board_api/` | Factory, Approvals, Event Store |
| 15 | Board Frontend | `modules/board_ui/` | Board API |
| 16 | Inter-Company Communication | `modules/inter_company/` | Connector, Approvals |
| 17 | Integration & E2E | `modules/integration/` | All |
| 18 | Efficiency & Diagnostics | `modules/efficiency/` | Event Store, Cost, Approvals, Orchestrator, Identity |
| 19 | LLM Providers | `modules/llm/` | Agent Runtime |

## 5. Repo structure

```text
ai-company/
├── MASTER_PLAN.md
├── pyproject.toml          # Python package configuration
├── Dockerfile              # Python application (api + agent runtime)
├── docker-compose.yml      # app + redis + (later) ui
├── .dockerignore
├── .python-version
├── .env.example
├── Makefile                # docker compose shortcuts
├── README.md
├── modules/
│   ├── storage/
│   │   ├── PLAN.md
│   │   ├── __init__.py
│   │   └── tests/
│   ├── event_store/
│   └── ... (17 modules)
├── data/                   # docker volume mount target — gitignore
│   ├── board.db
│   └── companies/<id>/...
└── deploy/                 # additional deploy artifacts (later)
```

### 5.1 Docker approach

- **All Python code runs inside the container.** No Python installation required on the host.
- Dev workflow: `docker compose run --rm app pytest …` or `make test`.
- Source code is mounted into the container (for live reload).
- `data/` is persisted as a named volume.
- Redis will be added as a separate service in compose (before Module 11).
- Network: containers are not directly exposed to the outside world; egress only through the Connector (eventually).

## 6. Cross-cutting disciplines

All modules follow:

- **Type safety:** mypy strict
- **Lint/format:** ruff
- **Test:** pytest, target 80%+ branch coverage
- **Logging:** structlog, JSON output, every log includes `company_id` + `agent_id` (where applicable)
- **Config:** pydantic-settings, env-based
- **Errors:** each module exposes its own exception classes (`acf.<module>.exceptions`)
- **Idempotency:** externally-effectful calls accept a `request_id` parameter
- **Dependency injection:** modules receive external dependencies via the constructor (mocks injected in tests)

## 7. Shared data model summary

Details are in each module's own PLAN.md. This is just the schema map:

**Board-level DB (`data/board.db`):**
- `users` — JWT identities
- `companies` — registered companies and their status
- `inter_company_links` — approved company-to-company connections
- `board_approvals` — cross-company approvals (e.g. inter-company links)

**Per-company DB (`data/companies/<id>/company.db`):**
- `agents` — org chart
- `events` — append-only audit log
- `messages` — agent messages (for fast querying in addition to events)
- `tool_calls`
- `expenses`, `pricing_catalog`, `recurring_subscriptions`
- `approvals`
- `memories`, `memory_embeddings`
- `clock_state`
- `workspaces` — quota metadata

## 8. Security and privacy principles

- **Workspace isolation:** Only the agent itself and its direct manager can read an agent's workspace. ID-based control at the Tool layer. Path traversal is impossible — agents don't specify file paths, they specify IDs.
- **Connector as single gateway:** All external HTTP, email, and payment calls go through the Connector. Allowlist managed in the Board.
- **Security veto:** An action rejected by the Security agent cannot be overridden (not even by the CEO).
- **Prompt injection defence:** The Connector converts external responses to structured form; raw content never flows into an agent's system prompt.
- **Secret management:** API keys are stored in a secret store invisible to agents; the agent says "use this service" and the Connector resolves the key.

## 9. Phase schedule (module-by-module)

```
Week 1-2:  Storage + Event Store + Clock          (foundation)
Week 3:    Identity + Cost                        (governance core)
Week 4:    Approvals + Memory                     (governance core)
Week 5:    Tools + Connector                      (agent surface)
Week 6-7:  Agent Runtime + Orchestrator           (heart of system)
Week 8:    Security Agent + Company Factory       (composition)
Week 9-10: Board API + Board UI                   (control plane)
Week 11:   Inter-Company                          (advanced)
Week 12:   Integration & E2E                      (stitch)
```

## 10. Definition of Done

When the project is fully complete:
- A Board user registers, logs in, and receives a JWT
- They say "new company", enter sector/budget/disk
- Within 30 seconds CEO + HR + Security are up, initial messaging begins
- CEO tells HR "hire two engineers", HR proposes candidates, once Board approves they are hired
- An engineer writes code, wants to rent a server → Connector → Board approval → expense deducted
- An agent "goes rogue" → Orchestrator detects → alerts their manager
- Pause/Resume works, the replay slider scrubs through history
- Two companies (with board approval) establish a vendor/customer relationship

## 10b. Efficiency & bottleneck detection (Module 18 summary)

A dedicated module that **passively** detects when a company is idle, stuck in a loop, unbalanced, or has a broken cost/output ratio. Subscribes to the event store, produces metrics over sliding windows, and emits a **Finding** when a threshold is crossed.

**Signal categories**
- Behavioural: loop, stall, message thrash, re-litigation, idle agent.
- Operational: tool retry storm, approval backlog, manager bottleneck.
- Economic: cost-without-output, token-to-progress ratio, unused subscription spend.
- Structural: org-chart load imbalance (pile-up at one manager), span-of-control exceeded.

**Outputs**
- Board UI: Efficiency tab (active alerts + trend charts + agent KPIs).
- Severity-tagged notifications (info/warn/critical) + optional auto-escalation event.
- CSV/JSON export for retrospective analysis.

**Architecture rules**
- Passive consumer (Redis Streams subscriber); no load on the critical path.
- Detectors are pure functions; each is unit-tested individually.
- Thresholds are in config and can be overridden per company.
- Never applies any action **autonomously** — only reports/recommends; acting is the responsibility of the Board or a manager.

## 11. Open questions (to be resolved in the integration phase)

- LLM provider key rotation / multi-key load balancing policy
- Production deploy format (Docker compose? Is a single node enough initially?)
- Backup/restore strategy (per-company DB snapshot)
- Audit log retention policy
- Multi-tenancy (multiple boards) — currently assumes a single board

## 12. This document is a living document

When each module is finished the corresponding checkbox in this file is updated. If a design decision changes, a revision date is noted in the "Core decisions" table. Conflicts between module plans are resolved by referring to this file — it is the single source of truth.

### Progress

- [x] 01 Storage
- [x] 02 Event Store
- [x] 03 Clock
- [x] 04 Identity & Org Chart
- [x] 05 Cost & Budget Engine
- [x] 06 Approval System
- [x] 07 Memory Subsystem
- [x] 08 Tool Layer
- [x] 09 Connector
- [x] 10 Agent Runtime
- [x] 11 Orchestrator
- [x] 12 Security Agent
- [x] 13 Company Factory
- [x] 14 Board Backend API
- [x] 15 Board Frontend
- [x] 16 Inter-Company Communication
- [x] 17 Integration & E2E
- [x] 18 Efficiency & Diagnostics
- [x] 19 LLM Providers (Mock / GitHub Models / Anthropic)
