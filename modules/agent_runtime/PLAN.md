# Module 10 — Agent Runtime

## Purpose

The lifecycle of a single agent. A Claude Agent SDK loop with a persona prompt, role, tools, and memory. Receives messages, reasons, uses tools, responds. Publishes heartbeats and generates a summary when a task is completed.

## Responsibility boundaries

**Inside scope:**
- Start/stop the agent process (per-agent asyncio task)
- Persona prompt loading (`persona_ref` from Identity, then template render)
- Conversation loop: incoming message → LLM call → tool calls → response → working memory append
- Recall workflow: at the start of each turn, query → recall → inject into context
- Task completion detection (LLM emits `<task_done>` block) → episode summary
- Heartbeat: `AGENT_HEARTBEAT` event on each turn
- Pause-aware: finish the current turn when pausing, do not accept new turns

**Outside scope:**
- Message queue (Orchestrator)
- Tool implementation (Tools)
- The LLM client itself (uses Claude Agent SDK)

## Dependencies

| Module | How |
|---|---|
| Identity | `Org.get(agent_id)` |
| Memory | `working_window`, `append_working`, `recall`, `remember_episode` |
| Tools | `for_role`, `claude_sdk_schema`, `invoke` |
| Event Store | heartbeat, task done |
| Cost | LLM token billing |
| Clock | pause signal (`is_accepting_messages`) |

## Public API

```python
class Agent:
    def __init__(self, agent_id: str, deps: AgentDeps): ...

    async def start(self) -> None: ...
    async def stop(self, drain: bool = True) -> None: ...

    async def deliver(self, message: IncomingMessage) -> None:
        """Called by Orchestrator. Adds to the queue; the agent task consumes it."""

    def status(self) -> AgentStatus: ...
    def health_signals(self) -> HealthSignals: ...

@dataclass
class AgentDeps:
    identity: Org
    memory: Memory
    tools: Tools
    events: EventStore
    cost: Pricing
    clock: Clock
    llm: LLMClient
    persona_loader: PersonaLoader

@dataclass(frozen=True)
class IncomingMessage:
    from_agent: str | None      # None → system / orchestrator / board
    content: str
    correlation_id: str | None
    kind: MessageKind            # USER_REQUEST | AGENT_MESSAGE | APPROVAL_DECIDED | ORPHAN | NOTIFY

class AgentStatus(str, Enum):
    IDLE       = "idle"
    WORKING    = "working"
    BLOCKED    = "blocked"     # pending approval
    UNHEALTHY  = "unhealthy"   # flagged by orchestrator
    STOPPED    = "stopped"

@dataclass(frozen=True)
class HealthSignals:
    last_heartbeat: datetime
    consecutive_errors: int
    parse_failures: int
    repeat_response_count: int
    avg_turn_seconds: float
```

## Persona loading

`persona_ref` → reads from `personas/<role>/<ref>.md` or DB. Template variables:
- `{{agent_id}}`
- `{{role}}`
- `{{company_id}}`
- `{{manager_name}}` / `{{direct_reports}}`
- `{{company_mission}}` (config from Factory)
- `{{available_tools}}`

Loader interface:
```python
class PersonaLoader(Protocol):
    def load(self, role: Role, ref: str, ctx: dict) -> str: ...
```

## Conversation loop

```
async def run_loop():
    while not stopping:
        if not clock.is_accepting_messages():
            await event(self._unblock_or_stop)
            continue

        msg = await self.queue.get()
        self._status = WORKING
        events.append(MESSAGE_DELIVERED, msg)
        memory.append_working(agent_id, WorkingItem(role="user", content=msg.content))

        # Recall context
        recall_query = msg.content[:500]
        hits = memory.recall(agent_id, recall_query, k=5)
        recall_block = format_recall(hits)

        # Build prompt
        system = persona_loader.load(role, ref, ctx) + "\n\n" + recall_block
        history = memory.working_window(agent_id, n=20)
        tool_schemas = tools.claude_sdk_schema(role)

        # LLM turn (Claude Agent SDK loop — tool use until stop)
        async for event_chunk in llm.run_turn(system, history, tool_schemas):
            if event_chunk.type == "tool_use":
                result = await tools.invoke(
                    agent_id=agent_id,
                    tool_name=event_chunk.name,
                    args=event_chunk.input,
                    correlation_id=msg.correlation_id,
                )
                # Feed tool result back to the LLM
                ...
            elif event_chunk.type == "text":
                memory.append_working(agent_id, WorkingItem(role="agent", content=event_chunk.text))

        events.append(AGENT_HEARTBEAT, {agent_id, status: 'idle'})

        # Task done detection
        if "<task_done>" in last_text:
            await self._summarize_and_remember(msg.correlation_id)

        self._status = IDLE
```

## Task summarization

Via a special marker in the agent output (`<task_done summary="..." />`) or a tool call (`mark_task_done`).

```
def _summarize_and_remember(correlation_id):
    items = memory.working_window_for_correlation(correlation_id)
    summary_prompt = "Summarise: what I did, results, learnings"
    summary = llm.completion(summary_prompt, context=items)
    memory.remember_episode(agent_id, summary, metadata={correlation_id})
```

## Pause compliance

When Clock `pause()` is called, `is_accepting_messages()` returns False. The agent:
- Finishes the current turn
- May still receive new messages via `deliver()` but does not consume from the queue
- `is_idle` becomes True (Clock counts this in the idle probe)

Resumes consuming from the queue when resumed.

## Health signals

Signal sent to the Orchestrator after each turn:
- `last_heartbeat` (timestamp)
- `consecutive_errors` (number of tool errors)
- `parse_failures` (LLM output did not match tool schema)
- `repeat_response_count` (ratio of last 5 responses with cosine similarity > 0.9)
- `avg_turn_seconds`

The Orchestrator uses these signals to determine an `unhealthy` verdict.

## Crash recovery

On process restart:
1. List of agents with `status='active'` from the `agents` table
2. A new Agent instance is created for each, `start()` is called
3. Working memory is restored from DB (already persistent)
4. If an in-flight turn was left incomplete: the last `AGENT_HEARTBEAT` is checked; if stale, starts with `unhealthy` flag — Orchestrator decides

## Edge cases

| Scenario | Behaviour |
|---|---|
| LLM call timeout | Retry x2 (exponential), then `consecutive_errors++` |
| Tool denied | Return error message to LLM, let agent try another approach |
| Pending approval — tool denied | Agent status BLOCKED; Orchestrator monitors; send informational message when approval arrives |
| Agent continuously sends messages to itself (self-loop) | Tools `send_message` permission_check: `to_agent != agent_id` |
| Persona prompt too long (>10k tokens) | Truncate warning + log |
| Working memory DB lock | WAL retry |

## Test scenarios

1. `test_start_loads_persona_and_status_idle`
2. `test_deliver_appends_to_queue`
3. `test_turn_processes_message_appends_working`
4. `test_recall_injected_in_context`
5. `test_tool_use_invokes_tools_layer`
6. `test_tool_denied_response_to_llm`
7. `test_task_done_marker_triggers_episode_save`
8. `test_pause_blocks_new_turns_completes_inflight`
9. `test_health_signals_track_repeat_responses`
10. `test_crash_restart_resumes_with_persisted_memory`
11. `test_self_message_blocked_by_tool_layer`
12. `test_llm_timeout_retries_then_reports_error`
13. `test_status_transitions`

## Error classes

```python
class AgentError(Exception): ...
class PersonaLoadError(AgentError): ...
class LLMFailure(AgentError): ...
class TurnTimeout(AgentError): ...
```

## Claude Agent SDK integration

Use the Claude Agent SDK loop. Tool definitions come from the Tool Layer. The `system` prompt is persona + recall. Conversation history is in `messages` format (user/assistant alternating). Tool result messages are returned in SDK format.

Caching: prompt caching (cache_control) for persona prompts and long system blocks — significantly reduces cost. Mandatory.

## Definition of Done

- [ ] Single agent can hold a dialogue with the SDK (tested with mock LLM)
- [ ] Tool usage working
- [ ] Recall and episodic save flow tested
- [ ] Pause compliance tested
- [ ] Health signals being generated
- [ ] Crash recovery tested
- [ ] Prompt caching active
- [ ] Coverage ≥ 85%

## File skeleton

```
modules/agent_runtime/
├── PLAN.md
├── __init__.py
├── agent.py
├── persona_loader.py
├── llm_client.py          # Claude Agent SDK wrapper
├── turn.py                # single-turn logic
├── health.py              # signal aggregation
├── personas/              # default templates
│   ├── ceo.md
│   ├── cfo.md
│   ├── cto.md
│   ├── hr.md
│   ├── security.md
│   └── engineer.md
├── exceptions.py
└── tests/
    ├── test_agent_lifecycle.py
    ├── test_turn.py
    ├── test_persona.py
    ├── test_recall_flow.py
    ├── test_pause.py
    └── test_health.py
```
