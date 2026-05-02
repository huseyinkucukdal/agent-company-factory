# Module 03 — Clock

## Purpose

Decouple company time from real time: **1 real hour = 1 company day**. Pause/resume support. Pause is "soft" — new messages stop but in-flight tasks complete. "Company day" ticks are published as events (the Cost engine runs monthly subscriptions based on these).

## Responsibility boundaries

**In scope:**
- Real-time → company-time conversion (linear, configurable rate)
- Pause/resume state machine: `running → pausing → paused → running`
- Transition from "pausing" to "paused": automatic when all agents become idle
- Periodic `DAY_TICK` event (emitted each time a company day completes)
- Test/simulation mode: accelerated time (1s real = 1 day)

**Out of scope:**
- Making agents idle (Orchestrator handles this; Clock only sends/receives signals)
- Monthly subscription billing (Cost handles this; Clock only triggers)

## Dependencies

| Module | How it is used |
|---|---|
| Storage (CompanyDB) | `clock_state` table |
| Event Store | `CLOCK_PAUSED`, `CLOCK_RESUMED`, `DAY_TICK` |
| Orchestrator | `is_company_idle(company_id)` Protocol — checked during pause |

`OrchestratorIdleProbe(Protocol)` is injected via constructor. Fake in tests.

## Public API

```python
class Clock:
    def __init__(self, company_id: str, db: CompanyDB,
                 events: EventStore,
                 idle_probe: OrchestratorIdleProbe,
                 rate: ClockRate = ClockRate.realtime()): ...

    async def start(self) -> None:
        """Start the background task: check for DAY_TICK every real minute +
        check for pausing→paused transition."""

    async def stop(self) -> None: ...

    def now_company(self) -> datetime:
        """Current company time."""

    def now_real(self) -> datetime: ...

    def state(self) -> ClockState: ...

    async def pause(self, requested_by: str) -> None:
        """state → pausing. Automatically transitions to paused when idle."""

    async def resume(self, requested_by: str) -> None:
        """state → running. Pause duration is subtracted from company time."""

    def is_accepting_messages(self) -> bool:
        """True when state == running."""

class ClockState(str, Enum):
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED  = "paused"

@dataclass(frozen=True)
class ClockRate:
    real_seconds_per_company_day: float = 3600.0  # 1 hour = 1 day
    @staticmethod
    def realtime() -> "ClockRate": return ClockRate(3600.0)
    @staticmethod
    def fast(speedup: float) -> "ClockRate":
        return ClockRate(3600.0 / speedup)
```

## State machine

```
        pause()                idle_probe true
running ───────────► pausing ─────────────────► paused
   ▲                    │                          │
   │ resume()           │ resume()                 │ resume()
   └────────────────────┴──────────────────────────┘
```

- `pause()` call is a no-op if already `paused`
- `resume()` can transition to running from any state
- A new `pause()` call while in `pausing` state is a no-op
- `pausing → paused` transition is checked by the background task every second

## Time math

If `real_seconds_per_company_day = R`:

```
company_seconds = (real_now - epoch_real) * (86400 / R) - paused_total_real * (86400 / R)
                 + paused_total_company_offset
```

Simpler approach: store `(epoch_real, epoch_company, paused_accumulated_real_seconds)` in state. Subtract paused duration when calling `now_company()`.

```python
def now_company(self):
    if self._state == PAUSED:
        return self._snapshot_company_at_pause
    elapsed_real = (now_real() - self._epoch_real).total_seconds()
    elapsed_real -= self._paused_total
    factor = 86400 / self.rate.real_seconds_per_company_day
    return self._epoch_company + timedelta(seconds=elapsed_real * factor)
```

## Persistence

```sql
CREATE TABLE clock_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
    state             TEXT NOT NULL,
    epoch_real        TEXT NOT NULL,
    epoch_company     TEXT NOT NULL,
    paused_total_sec  REAL NOT NULL DEFAULT 0,
    pause_started_at  TEXT,
    last_day_ticked   INTEGER NOT NULL DEFAULT 0,
    rate_seconds_per_day REAL NOT NULL
);
```

Crash recovery: on process restart, read from `clock_state`; if `pause_started_at` is set, continue from that point — the pause duration is counted as having elapsed in real time (alternative: counted as frozen; open question to discuss with the user).

## DAY_TICK logic

Background task runs every real minute:
```
current_day = floor(elapsed_company_seconds / 86400)
if current_day > last_day_ticked:
    for d in range(last_day_ticked+1, current_day+1):
        events.append(DAY_TICK, {"day": d, "ts_company": ...})
    last_day_ticked = current_day
```

If multiple days have been skipped (e.g. after a long pause followed by a resume), tick through each in order.

## Soft pause

`is_accepting_messages()` is the gate checked by Orchestrator before adding a message to the queue:
- `running` → True, message is added
- `pausing` or `paused` → False, message is rejected (sender receives a "company paused" notification)

In-flight tool calls are not interrupted. Once an agent's turn ends, the Orchestrator does not assign new work. When Orchestrator's `is_company_idle` callback returns true, `pausing → paused`.

## Test/simulation mode

In tests, `ClockRate.fast(3600)` → 1 real second = 1 company day, so a 30-day simulation runs in 30 seconds. DAY_TICKs are emitted the same way.

## Edge cases

| Scenario | Behavior |
|---|---|
| Pause called while already paused | No-op, log only |
| Resume called while already running | No-op |
| Clock adjustment (NTP stepped back) | Use a real-time monotonic source (`time.monotonic()` based) — not the system clock |
| Process crash during pausing | State remains pausing on restart; idle probe runs again |
| Idle probe continuously returns false | Remains in `pausing` state; no new messages are accepted until the user issues a resume (intended behavior) |
| Very high rate (1ms/day) | DAY_TICK flood — rate-limit test required; minimum 100ms window |

## Test scenarios

1. `test_now_company_advances_realtime` — 60 real seconds → 1 company day (rate=1h/day)
2. `test_pause_blocks_new_messages`
3. `test_pause_lets_inflight_finish` (idle probe first false, then true)
4. `test_pausing_to_paused_when_idle`
5. `test_resume_continues_company_time`
6. `test_paused_time_does_not_advance`
7. `test_day_tick_emitted_on_boundary`
8. `test_day_tick_catches_up_after_long_pause`
9. `test_persistence_roundtrip` — state is preserved after restart
10. `test_fast_mode_for_simulation`
11. `test_double_pause_idempotent`
12. `test_real_clock_jumps_backward_no_negative_company_time`

## Error classes

```python
class ClockError(Exception): ...
class InvalidStateTransition(ClockError): ...
```

## Definition of Done

- [ ] State machine + persistence working
- [ ] DAY_TICKs are emitted at the correct times
- [ ] Fast mode is testable
- [ ] Soft pause is tested (idle probe integration)
- [ ] Crash recovery is tested
- [ ] Coverage ≥ 85%
- [ ] Monotonic clock usage verified

## File skeleton

```
modules/clock/
├── PLAN.md
├── __init__.py
├── clock.py
├── state.py            # ClockState, ClockRate
├── protocols.py        # OrchestratorIdleProbe
├── exceptions.py
├── migrations/001_init.sql
└── tests/
    ├── test_time.py
    ├── test_pause.py
    ├── test_day_tick.py
    └── test_persistence.py
```

## Open questions

- If a crash occurs during a pause, should the pause duration continue to elapse in real time or be considered frozen? Default recommendation: the time the process was down is counted toward `paused_total` (i.e., when resumed, no time appears to have passed).
