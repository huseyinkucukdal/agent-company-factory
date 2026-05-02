# Agent Company Factory

> A platform for creating and operating **autonomous AI companies** — complete with a CEO, org chart, budget, approvals, and a real-time Board control panel.

Each company is a team of AI agents that do real work: write code, send emails, run ads, hire and fire employees. Every action that touches the outside world requires human (Board) approval. Everything is observable and fully replayable.

---

## What is this?

You spin up a company. Within seconds a **CEO**, **HR**, and **Security** agent come online. The CEO can ask HR to hire engineers. Engineers can use tools to write code, call APIs, or rent servers. Every expense — even tiny ones — needs Board sign-off. The Security agent has veto power over anything suspicious.

Think of it as a fully auditable, budget-constrained, human-supervised AI startup running inside Docker.

**Key properties:**
- Isolated per-company SQLite DB + filesystem workspace
- All external calls go through a Connector (allowlist, rate limits, secret management)
- Prompt-injection defence: external responses are converted to structured form before reaching agents
- 1 real hour = 1 company day (configurable clock)
- Board UI (Next.js) with live updates via Server-Sent Events

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI |
| Agent runtime | Claude Agent SDK |
| Message queue | Redis Streams |
| Database | SQLite per company + board-level SQLite |
| Vector store | sqlite-vss (embedded) |
| Auth | JWT, multi-user |
| Frontend | Next.js + Tailwind CSS |
| Runtime | Docker (everything runs in containers) |

---

## Quick start

The entire application runs inside Docker. **No Python required on the host.**

```bash
make build         # build the Docker image
make test          # run the full test suite
make shell         # open a bash shell inside the container
make lint          # run ruff
make typecheck     # run mypy
```

---

## Project structure

```
modules/
├── storage/          # 01 — per-company SQLite DB + workspace filesystem
├── event_store/      # 02 — append-only audit log
├── clock/            # 03 — company-time simulation
├── identity/         # 04 — org chart, roles, hire/fire rules
├── cost/             # 05 — budgets, expenses, subscriptions
├── approvals/        # 06 — Board approval workflows
├── memory/           # 07 — agent memory + vector embeddings
├── tools/            # 08 — tool layer (file, web, code, etc.)
├── connector/        # 09 — external HTTP/email/payment gateway
├── agent_runtime/    # 10 — agent loop (Claude SDK)
├── orchestrator/     # 11 — message routing between agents
├── security_agent/   # 12 — Security agent + veto logic
├── factory/          # 13 — company bootstrap
├── board_api/        # 14 — FastAPI REST + SSE backend
├── board_ui/         # 15 — Next.js control panel
├── inter_company/    # 16 — Board-approved company-to-company comms
├── integration/      # 17 — end-to-end tests
├── efficiency/       # 18 — diagnostics and performance
└── llm/              # 19 — LLM provider abstraction
data/                 # runtime DBs and workspaces (Docker volume)
deploy/               # production compose files
```

Each module has its own `PLAN.md` with detailed design, its own tests, and a clean public API. Modules depend on each other through `Protocol`/`abc.ABC` interfaces — making unit tests run with mocks.

---

## Development approach

Modules are built **horizontally**: finish one module completely (with tests) before moving to the next. This avoids throwaway MVP code. See [MASTER_PLAN.md](MASTER_PLAN.md) for the full module roadmap and dependency order.

Cross-cutting standards across all modules:
- **Type safety:** `mypy --strict`
- **Lint/format:** `ruff`
- **Tests:** `pytest`, 80%+ branch coverage target
- **Logging:** `structlog` JSON output, every log includes `company_id` + `agent_id`
- **Config:** `pydantic-settings`, env-based

---

## Security model

- **Workspace isolation** — agents access files by ID, not by path. Path traversal is impossible.
- **Connector as single gateway** — all outbound calls (HTTP, email, payments) go through the Connector with an allowlist managed from the Board.
- **Security veto** — a rejection by the Security agent cannot be overridden, not even by the CEO.
- **Secret management** — API keys are stored in a secret store invisible to agents.
- **Prompt injection defence** — external responses are converted to structured data before reaching any agent prompt.

---

## Full design

See [MASTER_PLAN.md](MASTER_PLAN.md) for vision, architecture decisions, data model, security principles, and the full phase schedule.
