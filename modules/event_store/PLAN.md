# Module 02 — Event Store

## Purpose

Append-only audit log + live feed source + replay. Everything is an event: message, tool call, approval request, expense, agent state change, security alert. The Board's live monitoring screen and replay slider are built on top of this module.

## Responsibility boundaries

**In scope:**
- Event writing (append-only — no UPDATE/DELETE)
- Event reading: streaming from `since=event_id`, filtered queries
- Subscribe API: in-process callback listeners
- Generator for SSE bridge (used by Board API)
- Event type catalog (typed payloads — pydantic)
- Grouping by correlation ID (`task_id`, `request_id`)

**Out of scope:**
- Interpreting events (Orchestrator/Board UI handles this)
- Cross-company aggregation (handled by Board API)
- Persistence retention policy (to be added later)

## Dependencies

| Module | How it is used |
|---|---|
| Storage | DB connection via `CompanyDB.connect()` |

## Public API

```python
# modules/event_store/store.py
class EventStore:
    def __init__(self, company_db: CompanyDB): ...

    def append(self, kind: EventKind, payload: dict,
               actor: AgentRef | None = None,
               correlation: CorrelationRef | None = None) -> Event:
        """Atomic append, returns a monotonic event_id."""

    def read(self,
             since: int | None = None,
             until: int | None = None,
             kinds: list[EventKind] | None = None,
             actor: str | None = None,
             correlation: str | None = None,
             limit: int = 1000) -> list[Event]: ...

    async def stream(self, since: int = 0) -> AsyncIterator[Event]:
        """Live tail. Source for SSE. Backpressure: if the listener is slow,
        the store does not fill its own buffer — async generator."""

    def subscribe(self, callback: Callable[[Event], None]) -> SubscriptionHandle:
        """In-process synchronous listener. Fire-and-forget after append."""

@dataclass(frozen=True)
class Event:
    id: int                          # monotonic, per-company
    company_id: str
    kind: EventKind                  # str enum
    payload: dict                    # typed (per kind)
    actor_agent_id: str | None
    correlation_id: str | None       # task_id or request_id
    ts_company: datetime             # company time
    ts_real: datetime                # real time

class EventKind(str, Enum):
    # messaging
    MESSAGE_SENT = "message.sent"
    MESSAGE_DELIVERED = "message.delivered"
    # tool
    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"
    TOOL_DENIED = "tool.denied"
    # agent lifecycle
    AGENT_CREATED = "agent.created"
    AGENT_FIRED = "agent.fired"
    AGENT_HIRED = "agent.hired"
    AGENT_HEARTBEAT = "agent.heartbeat"
    AGENT_HEALTH_ALERT = "agent.health_alert"
    # finance
    EXPENSE_CHARGED = "expense.charged"
    BUDGET_WARNING = "budget.warning"
    BUDGET_BLOCKED = "budget.blocked"
    # approvals
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    APPROVAL_TIMEOUT = "approval.timeout"
    # security
    SECURITY_FLAG = "security.flag"
    SECURITY_VETO = "security.veto"
    # connector
    EXTERNAL_CALL = "connector.external_call"
    # workspace
    QUOTA_WARNING = "quota.warning"
    QUOTA_EXCEEDED = "quota.exceeded"
    # clock
    CLOCK_PAUSED = "clock.paused"
    CLOCK_RESUMED = "clock.resumed"
    DAY_TICK = "clock.day_tick"
    # company
    COMPANY_CREATED = "company.created"
    COMPANY_CLOSED = "company.closed"
    # inter-company
    LINK_REQUESTED = "link.requested"
    LINK_APPROVED = "link.approved"
```

The pydantic model for each `kind`'s payload is in `payloads.py`:
```python
class MessageSentPayload(BaseModel):
    from_agent: str
    to_agent: str
    content: str
    thread_id: str | None
```

## Persistence

```sql
-- per-company DB
CREATE TABLE events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT    NOT NULL,
    payload_json    TEXT    NOT NULL,
    actor_agent_id  TEXT,
    correlation_id  TEXT,
    ts_company      TEXT    NOT NULL,
    ts_real         TEXT    NOT NULL
);
CREATE INDEX idx_events_kind         ON events(kind);
CREATE INDEX idx_events_actor        ON events(actor_agent_id);
CREATE INDEX idx_events_correlation  ON events(correlation_id);
CREATE INDEX idx_events_ts           ON events(ts_real);
```

`AUTOINCREMENT` is used to keep IDs monotonic. SQLite's non-AUTOINCREMENT rowid would also suffice, but this is safer.

## Subscribe / stream architecture

We support two different models:

1. **Synchronous subscribe** (`subscribe(callback)`): callbacks are called sequentially inside `append()`. For fast listeners (Orchestrator loop detection, Cost engine reaction).

2. **Async stream** (`stream(since)`): for SSE. Implementation: `asyncio.Queue` per-stream + `loop.call_soon_threadsafe(queue.put_nowait, event)` during `append()`. If the consumer is slow, the queue fills up; when the max size is exceeded, the oldest event is dropped and a `stream_drop` warning event is emitted (drop events are exempt from this mechanism to avoid recursion).

When the consumer disconnects, the subscription handle is garbage collected.

## Performance targets

- Append: < 5ms (SQLite WAL mode)
- Writing 10k events + sequential read: < 30s
- Should be able to support 50 concurrent SSE listeners
- In a company with 100k events, `read(since=99000)` < 100ms (thanks to indexes)

## Replay support

Using `read(until=event_id)` to retrieve all events up to a snapshot is sufficient for the Board UI replay slider. Board UI uses `ts_company` to display events on a timeline.

Compression/snapshot in the future: aggregating daily events into an `events_snapshot_<day>` table (not implemented yet).

## Edge cases

| Scenario | Behavior |
|---|---|
| Thousands of events with the same `correlation_id` | No problem, indexed |
| Very large payload (>1MB JSON) | Reject — `payload_json` upper limit is 256KB; above that, raise `payload_too_large` |
| DB lock during append | Retry with SQLite WAL (3 times, exponential backoff); raise if unsuccessful |
| Subscribe callback raises an exception | Catch + log + do not affect other subscribers |
| Stream consumer does not respond for 30s | Subscription is closed, automatic unsubscribe |
| Event with a past timestamp (during replay) | `ts_company` is in the past, `ts_real` is now — stored as-is; queries are ordered by `id` anyway |

## Test scenarios

1. `test_append_returns_monotonic_id`
2. `test_append_persists_payload_roundtrip`
3. `test_append_unknown_kind_rejected`
4. `test_payload_too_large_rejected`
5. `test_read_filter_by_kind`
6. `test_read_filter_by_actor`
7. `test_read_since_returns_only_newer`
8. `test_subscribe_callback_called_on_append`
9. `test_subscribe_exception_does_not_break_others`
10. `test_stream_yields_in_order`
11. `test_stream_backpressure_drops_oldest_on_overflow`
12. `test_concurrent_appends_serialized`
13. `test_correlation_grouping`
14. `test_replay_until_works`

## Error classes

```python
class EventStoreError(Exception): ...
class UnknownEventKind(EventStoreError): ...
class PayloadTooLarge(EventStoreError): ...
class PayloadValidationError(EventStoreError): ...
```

## Definition of Done

- [ ] `append`, `read`, `stream`, `subscribe` are working
- [ ] All `EventKind` payload models are defined
- [ ] 14 tests pass
- [ ] WAL retry mechanism is working
- [ ] SSE backpressure is tested
- [ ] Coverage ≥ 85%
- [ ] Performance targets measured (roughly)

## File skeleton

```
modules/event_store/
├── PLAN.md
├── __init__.py
├── store.py                # EventStore
├── kinds.py                # EventKind enum
├── payloads.py             # pydantic models
├── exceptions.py
├── migrations/001_init.sql
└── tests/
    ├── test_append.py
    ├── test_read.py
    ├── test_stream.py
    └── test_subscribe.py
```
