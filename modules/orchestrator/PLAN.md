# Module 11 — Orchestrator

## Purpose

Intra-company message queue (Redis Streams). Per-agent consumer. Loop detection, rate limiting, deadlock detector, agent health tracking. With a pause signal, no new messages are accepted but in-flight ones are completed.

## Responsibility boundaries

**Inside:**
- Redis Streams with per-company / per-agent queues
- Message routing: `from_agent → to_agent` or broadcast
- Loop detection (semantic + structural)
- Rate limiting per agent (messages per second/minute)
- Deadlock detection (no movement for extended time)
- Health monitoring → fire suggestion to Identity (alarm event)
- Pause: `is_accepting_messages` from Clock; new messages rejected
- "Idle probe" to Clock — are all agents idle during pause?
- Crash recovery: Redis stream pending entries

**Outside:**
- Agent-internal LLM logic (Agent Runtime)
- Tool decisions (Tools)
- Inter-company routing (Inter-Company)

## Dependencies

| Module | How |
|---|---|
| Redis | streams + consumer groups |
| Agent Runtime | `agent.deliver(msg)` |
| Identity | message source/target validation, fire suggestion |
| Event Store | all message/health/loop events |
| Clock | `is_accepting_messages`, `idle_probe` |

## Public API

```python
class Orchestrator:
    def __init__(self, company_id: str, redis: Redis,
                 identity, agents: AgentDirectory,
                 events, clock): ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def send(self, *, from_agent: str, to_agent: str,
                   content: str, correlation_id: str | None = None,
                   kind: MessageKind = MessageKind.AGENT_MESSAGE) -> SendResult: ...

    async def system_send(self, to_agent: str, content: str,
                          kind: MessageKind) -> SendResult:
        """Messages originating from the Orchestrator/Board."""

    def is_company_idle(self) -> bool:
        """All agents IDLE + queue empty + no in-flight tasks."""

    def health(self, agent_id: str) -> AgentHealth: ...

    async def restart_agent(self, agent_id: str, reason: str) -> None: ...

class SendResult(str, Enum):
    QUEUED = "queued"
    REJECTED_PAUSED = "rejected_paused"
    REJECTED_RATE_LIMIT = "rejected_rate_limit"
    REJECTED_LOOP = "rejected_loop"
    REJECTED_INVALID_TARGET = "rejected_invalid_target"

class MessageKind(str, Enum):
    AGENT_MESSAGE = "agent_message"
    USER_REQUEST = "user_request"
    APPROVAL_DECIDED = "approval_decided"
    NOTIFY = "notify"
    ORPHAN = "orphan"
```

## Redis Stream layout

```
acf:co:<company_id>:queue:<agent_id>     # each agent's inbox (stream)
acf:co:<company_id>:rate:<agent_id>      # rate limit token bucket
acf:co:<company_id>:msglog:<thread_id>   # thread-based recent messages for loop detection
acf:co:<company_id>:agent_status:<id>    # heartbeat, status snapshot
```

Consumer group: one consumer per agent (Agent Runtime task). Delivery confirmed via XACK.

## Loop detection

Two-layered:

**Structural (cycle):**
- `(from, to)` pairs from the last N messages are added to a graph
- If the same pair appears > 5 times consecutively → `LOOP_DETECTED`
- A→B→A→B cycle > 3 repetitions → alarm

**Semantic (content repeat):**
- Content embeddings of the last 5 messages sent to the same `to_agent`
- If 3+ messages have cosine similarity > 0.9 → "broken record" signal

When threshold is exceeded:
1. `agent.health_alert` event
2. Message returns REJECTED_LOOP
3. After 3 alerts, Orchestrator suspends the agent and sends a `notify` to the manager

## Rate limit

Token bucket: `60 messages/min` per agent. If exceeded: `REJECTED_RATE_LIMIT` + "slow down" notify to agent.

Configurable. Special roles (Security, HR) may have different limits.

## Deadlock detection

All agents `IDLE` and queue empty is normal. However:
- At least one agent `BLOCKED` (pending approval) and 30 real-time minutes have passed → `deadlock_warning` event to Board

All agents idle and last message was 1 hour (real) ago → "company stuck" event, notify CEO.

## Pause flow

Orchestrator subscribes to Clock events:
- `CLOCK_PAUSED` (when transitioning pausing→paused) → consumers are already not receiving new messages (send rejected); pending ones continue to be processed
- `CLOCK_RESUMED` → normal flow resumes

`Clock.idle_probe` call returns `is_company_idle()`.

## Health monitoring

Check every 30 seconds per agent:
```
signals = agent.health_signals()
score = compute_score(signals)   # 0..1
if score < 0.4:
    events.append(AGENT_HEALTH_ALERT, {agent_id, signals, suggestion})
    notify_manager(agent_id, "agent unhealthy, consider firing")
```

Score formula:
- consecutive_errors > 3 → -0.3
- parse_failures rate > 50% → -0.3
- repeat_response_count > 0.7 → -0.4
- last_heartbeat > 5 min → -0.5

## Crash recovery

Pending entries (Redis XPENDING) on restart:
- Pending message > 10 min → consumer crash assumed; redeliver or move to dead-letter
- Dead-letter stream: `acf:co:<id>:dlq` — visible on Board

## Edge cases

| Scenario | Behaviour |
|---|---|
| from_agent fired | REJECTED_INVALID_TARGET |
| to_agent fired | REJECTED_INVALID_TARGET (notify sender "agent fired") |
| Cross-company message | Rejected directly; Inter-Company module must be used |
| Duplicate message (correlation_id) | Idempotent: not re-enqueued if already queued |
| Redis down | Circuit breaker — `send` raises; agents cannot connect to queue, status STOPPED + alarm |
| Content too large (>256KB) | Rejected |
| Recursive system_send | Allowed but rate limit does not apply to Orchestrator (system exempt) |

## Test scenarios

1. `test_send_queued_when_running`
2. `test_send_rejected_when_paused`
3. `test_send_rejected_invalid_target`
4. `test_rate_limit_kicks_in`
5. `test_structural_loop_detected_after_threshold`
6. `test_semantic_loop_detected_high_similarity`
7. `test_loop_alert_after_three_strikes_suspends_agent`
8. `test_deadlock_warning_after_blocked_30min`
9. `test_company_stuck_warning_after_idle_1h`
10. `test_health_score_unhealthy_alerts_manager`
11. `test_crash_recovery_redelivers_pending`
12. `test_dlq_for_unprocessable_messages`
13. `test_system_send_bypasses_rate_limit`
14. `test_idle_probe_true_when_all_idle_queue_empty`
15. `test_redis_disconnect_circuit_breaker`

## Error classes

```python
class OrchestratorError(Exception): ...
class RedisUnavailable(OrchestratorError): ...
class InvalidTarget(OrchestratorError): ...
```

## Definition of Done

- [ ] Redis Streams integration (consumer groups)
- [ ] send flow complete (rate limit + loop + pause checks)
- [ ] Loop detection (structural + semantic) tested
- [ ] Health monitoring + manager notify tested
- [ ] Pause/Resume integrated with Clock
- [ ] Crash recovery tested (redis-py mock or real redis)
- [ ] DLQ mechanism
- [ ] Coverage ≥ 85%

## File skeleton

```
modules/orchestrator/
├── PLAN.md
├── __init__.py
├── orchestrator.py
├── streams.py             # Redis stream wrappers
├── rate_limit.py
├── loop_detector.py
├── deadlock.py
├── health_monitor.py
├── exceptions.py
└── tests/
    ├── test_send.py
    ├── test_loops.py
    ├── test_rate_limit.py
    ├── test_health.py
    ├── test_deadlock.py
    ├── test_pause.py
    └── test_crash_recovery.py
```

## Open questions

- Health score thresholds (0.4 cutoff) require tuning — to be adjusted in the integration phase.
- How will the loop detector distinguish between an agent consistently discussing the same task? Heuristic: not just repetition, but repetition + lack of progress (no new tool calls).
