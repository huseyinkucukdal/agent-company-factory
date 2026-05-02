# Module 18 — Efficiency & Diagnostics

## Purpose

Automatically detecting when a company is operating inefficiently and surfacing that to the Board. It does not apply any actions on its own; it generates signals, reports, and makes recommendations. A passive consumer that adds no load to the critical path.

## Scope

- **Sliding window** metrics from event store + cost + approvals + orchestrator + identity.
- **Finding** (warning record) generation based on configurable thresholds.
- `/companies/{id}/efficiency/*` endpoints in the Board API.
- New "Efficiency" tab in Board UI (to be added in Module 15 round 2; only the API will be ready for now).

## Out of scope

- Automatic corrective actions (firing agents, reassigning tasks). These are HR / manager workflows.
- ML-based anomaly detection — this first iteration is entirely rule-based (deterministic, debuggable).

## Architecture

```
event_store (Redis Streams + sqlite append log)
        │
        ▼  consumer-group "efficiency-{company_id}"
┌──────────────────────────┐
│ EfficiencyService         │
│   ├─ MetricStore (sliding)│      detectors are pure-functions:
│   ├─ Detectors[]          │ ───► detect(window, ctx) -> list[Finding]
│   └─ FindingStore (sqlite)│
└──────────────────────────┘
        │
        ▼
   Board API   ──►  Board UI (Efficiency tab)
```

- **MetricStore**: count / sum / p50/p95 over a sliding window (in-memory ring buffer + periodic SQLite snapshot).
- **Detectors**: each is a single file with a single `detect()` function. Stateless: receives a window snapshot, returns zero or more `Finding`s.
- **FindingStore**: per-company SQLite table (`findings`); handles open/close and deduplication (if an open finding already exists for the same agent + same category, it does not open another one — only updates `last_seen`).

## Detector catalogue (first iteration)

| Code | Name | Window | Threshold (default) | Severity |
|---|---|---|---|---|
| `loop.tool_repeat` | Same agent + same tool call repeated | 5 min | ≥4 | warn |
| `loop.message_repeat` | Same agent repeating a message with the same content | 10 min | ≥3 | warn |
| `stall.no_event` | No event on an active task | 15 company-min | — | warn |
| `stall.idle_agent` | Agent producing no events at all | 4 hours | — | info |
| `approvals.backlog` | Pending approval age p95 | 30 min | — | warn |
| `approvals.spam` | Number of approvals generated per hour | 1 hour | >50 | warn |
| `cost.no_output` | Spending present, no deliverable | 1 hour | >$5 + 0 new files/commits | warn |
| `cost.token_progress_ratio` | Tokens / new content bytes | 1 hour | >50k tokens & <1KB | info |
| `tool.error_storm` | Error rate for same tool | 10 min | >30% and ≥10 calls | critical |
| `org.manager_bottleneck` | Accumulation at a single manager | 1 hour | top1 > 60% | warn |
| `org.span_of_control` | Number of direct reports | instant | >8 | info |
| `decision.relitigation` | Reopening a closed decision | 24 hours | same corr_id ≥2 openings | warn |

Each detector is a separate file: `modules/efficiency/detectors/{code}.py`. Adding a new detector = one file + one test + adding to the registry.

## Data model

New tables in `data/companies/{id}/company.db`:

```sql
CREATE TABLE efficiency_findings (
  id              TEXT PRIMARY KEY,
  detector_code   TEXT NOT NULL,
  severity        TEXT NOT NULL,              -- info | warn | critical
  subject_type    TEXT NOT NULL,              -- agent | manager | company | tool | approval
  subject_id      TEXT,
  opened_at       INTEGER NOT NULL,           -- real epoch ms
  last_seen_at    INTEGER NOT NULL,
  closed_at       INTEGER,
  occurrences     INTEGER NOT NULL DEFAULT 1,
  evidence        TEXT NOT NULL,              -- JSON: measured values, sample event IDs
  recommendation  TEXT,                        -- summary recommendation for agent/manager
  status          TEXT NOT NULL DEFAULT 'open' -- open | acknowledged | closed
);
CREATE INDEX idx_findings_open ON efficiency_findings(status, severity, opened_at);

CREATE TABLE efficiency_metrics_snapshot (
  ts          INTEGER NOT NULL,
  metric_key  TEXT NOT NULL,                 -- e.g. "cost.usd_per_hour"
  value       REAL NOT NULL,
  PRIMARY KEY (ts, metric_key)
);
```

## Public API (to be added to Board API)

```
GET  /companies/{id}/efficiency/findings?status=open&severity=warn,critical
GET  /companies/{id}/efficiency/findings/{finding_id}
POST /companies/{id}/efficiency/findings/{finding_id}/ack    # operator+
POST /companies/{id}/efficiency/findings/{finding_id}/close  # operator+
GET  /companies/{id}/efficiency/metrics?keys=...&from=...&to=...
GET  /companies/{id}/efficiency/config       # thresholds
PATCH /companies/{id}/efficiency/config      # admin only, per-company override
```

SSE: `efficiency.finding.opened` / `efficiency.finding.closed` event kinds will be added to the existing `/companies/{id}/stream` events (two records added to Module 02 kinds.py).

## Minor changes required in other modules

- **event_store/kinds.py**: add `efficiency.finding.opened`, `efficiency.finding.closed`, `efficiency.metric.snapshot` (optional).
- **board_api/routes/**: `efficiency.py` router.
- **factory/bootstrap.py**: `EfficiencyService` is also started when a company is initialised (best-effort, fail-open: if it crashes the company continues running).
- **clock/**: Window calculations use real time (not company time), since we are showing this to a human operator. Single exception: `stall.no_event` may also accept company time.
- **MASTER_PLAN.md**: Module 18 added to the module list (done).

## Configuration

`modules/efficiency/thresholds.toml` — default values. Per-company override in the `efficiency_config` table (key/value JSON). Admin can change via Board UI.

## Test strategy

- **Unit**: synthetic event list → expected finding list for each detector. Easy because they are pure functions.
- **Integration**: connect `EfficiencyService` to a fake event store, run a scenario script (example: 5 identical tool calls within 10 sec → `loop.tool_repeat` warn).
- **Property-based**: as the number of events in the window grows, the FindingStore size should not grow linearly (is deduplication working correctly?).
- Coverage target 85%+.

## Performance budget

- Detector loop is triggered every 5 seconds or every 100 events (whichever comes first).
- Per-company CPU < 1%, RAM < 30 MB limit.
- **Zero** synchronous calls to the critical path (agent runtime, approvals decide); consumer is a separate task.

## Roadmap

**Round 1 (first delivery of this module)**
- MetricStore + FindingStore + 6 detectors (loop.tool_repeat, stall.no_event, approvals.backlog, cost.no_output, tool.error_storm, org.manager_bottleneck).
- API endpoints + SSE events.
- Pytest 85%+.

**Round 2**
- Remaining 6 detectors.
- Per-company config override + threshold editor in Board UI.
- CSV/JSON export.

**Round 3 (optional)**
- ML-based anomaly detection (e.g. deviation from cost trend) — only if rule-based detectors prove insufficient.
- Recommendation engine: can automatically open a task for HR — "Agent X is in a loop, escalation" (with Board approval).

## Definition of Done

- 6 detectors working, producing correct findings in synthetic scenarios.
- API endpoints exposed to Board users, role-gated.
- Consumer runs in a separate task; stopping/restarting does not affect the company lifecycle.
- `pytest modules/efficiency/tests/` green.
- MASTER_PLAN Module 18 checkbox ticked.
