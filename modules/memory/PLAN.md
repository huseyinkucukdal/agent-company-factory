# Module 07 — Memory Subsystem

## Purpose

Short- and long-term memory per agent. Task summaries, persistent facts, last N messages. Recall via vector search. Separate from workspace files (workspace = "what the agent writes"; memory = "the agent's inner mind").

## Three-layer design

| Layer | Content | Storage | Recall |
|---|---|---|---|
| **Working** | Last N (e.g. 50) messages/turns | Fast: DB + RAM cache | Direct slice |
| **Episodic** | Summaries of completed tasks | DB + embedding | Vector search |
| **Semantic** | Persistent facts ("My CEO is Ahmet", "my company is fintech") | DB + embedding | Key or vector |

## Responsibility boundaries

**Inside scope:**
- Working memory: append, read last-N, sliding window
- Episodic: written as a summary when the agent completes a task (summarisation is the Agent Runtime's job; Memory only stores it)
- Semantic: key-value + optional embedding
- Vector search (sqlite-vss): query → top-k
- Per-agent isolation
- A manager can read a subordinate's memory (same rule as workspace — checked via Identity)

**Outside scope:**
- Summarisation algorithm (Agent Runtime does it with LLM)
- Embedding model decision (config; sentence-transformers `all-MiniLM-L6-v2` default)

## Dependencies

| Module | How |
|---|---|
| Storage (CompanyDB) | `memories`, `memory_embeddings` |
| Identity | manager check |
| Embeddings | local sentence-transformers or Anthropic embedding endpoint |

## Public API

```python
class Memory:
    def __init__(self, db: CompanyDB,
                 identity: IdentityProvider,
                 embedder: Embedder,
                 working_size: int = 50): ...

    # Working
    def append_working(self, agent_id: str, item: WorkingItem) -> None: ...
    def working_window(self, agent_id: str, n: int | None = None) -> list[WorkingItem]: ...
    def clear_working(self, agent_id: str) -> None: ...   # after task completion

    # Episodic
    def remember_episode(self, agent_id: str, summary: str,
                         metadata: dict | None = None) -> str: ...

    # Semantic
    def remember_fact(self, agent_id: str, key: str, value: str,
                      embed: bool = True) -> None: ...
    def get_fact(self, agent_id: str, key: str) -> str | None: ...
    def all_facts(self, agent_id: str) -> dict[str, str]: ...

    # Recall (cross-kind)
    def recall(self, agent_id: str, query: str, k: int = 5,
               kinds: list[Kind] | None = None,
               for_reader: str | None = None) -> list[RecallHit]:
        """for_reader: for a manager to read their subordinate's memory.
        An Identity check is performed."""

    def forget(self, agent_id: str, item_id: str) -> None: ...

@dataclass(frozen=True)
class WorkingItem:
    role: str            # "user" | "agent" | "tool"
    content: str
    ts_company: datetime
    metadata: dict

class Kind(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"

@dataclass(frozen=True)
class RecallHit:
    kind: Kind
    item_id: str
    content: str
    score: float
    metadata: dict

class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...
    @property
    def dim(self) -> int: ...
```

## Persistence

```sql
CREATE TABLE memory_working (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata_json TEXT,
    ts_company  TEXT NOT NULL,
    ts_real     TEXT NOT NULL
);
CREATE INDEX idx_working_agent_ts ON memory_working(agent_id, id DESC);

CREATE TABLE memories (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,             -- episodic | semantic
    key         TEXT,                      -- for semantic
    content     TEXT NOT NULL,
    metadata_json TEXT,
    ts_real     TEXT NOT NULL
);
CREATE INDEX idx_memories_agent_kind ON memories(agent_id, kind);
CREATE UNIQUE INDEX idx_memories_semantic_key ON memories(agent_id, key)
    WHERE kind = 'semantic';

-- sqlite-vss
CREATE VIRTUAL TABLE memory_embeddings USING vss0(
    embedding(384)        -- MiniLM-L6-v2 dimensions
);
-- mapping
CREATE TABLE memory_vec_map (
    rowid       INTEGER PRIMARY KEY,
    memory_id   TEXT NOT NULL UNIQUE,
    agent_id    TEXT NOT NULL
);
```

## Working memory rotation

When `working_size` is exceeded, the oldest items are not deleted — they remain in `memory_working`, but `working_window(agent_id)` returns only the last N. `working_window(agent_id, n=1000)` is possible for historical archival.

When a task is completed, Agent Runtime does the following:
1. Retrieves all messages for the task via `working_window(agent_id, n=ALL_FOR_TASK)`
2. Summarises with LLM
3. Writes via `remember_episode(agent_id, summary)`
4. Optional `clear_working` (if a clean start for the new task is desired)

## Recall behaviour

```python
def recall(agent_id, query, k=5, kinds=None, for_reader=None):
    if for_reader and for_reader != agent_id:
        if not identity.can_read_workspace(for_reader, agent_id):
            raise PermissionDenied
    qvec = embedder.embed(query)
    # vss search → top-k memory_ids
    # filter kinds
    # join memories table
    return [RecallHit(...)]
```

Threshold: if relevance score < 0.3, no result is returned (filters out low-quality matches).

## Embedder abstraction

Two implementations:
- `LocalEmbedder` — sentence-transformers (default)
- `AnthropicEmbedder` — Anthropic API embedding endpoint (if needed in the future)

Selected via config in the constructor. `FakeEmbedder` produces deterministic vectors in tests.

## Edge cases

| Scenario | Behaviour |
|---|---|
| Same semantic key twice | UPSERT (new replaces old) |
| Recall query is empty string | Returns `[]` |
| Agent deleted, does memory remain? | Retained (for replay) but excluded from recall (`status='fired'` filter) |
| Embedding model changed, old vectors incompatible | `dim` mismatch detected; migration job → recompute all embeddings |
| Very large episode (10MB text) | 256KB upper limit; above this is rejected |
| Fractional embedding (zero vector) | Score drops in query, normal behaviour |

## Test scenarios

1. `test_append_and_read_working`
2. `test_working_window_returns_last_n`
3. `test_remember_episode_persists`
4. `test_remember_fact_upsert`
5. `test_recall_returns_relevant`
6. `test_recall_filters_by_kind`
7. `test_recall_threshold_filters_low_score`
8. `test_recall_other_agent_denied`
9. `test_recall_other_agent_as_manager_allowed`
10. `test_forget_removes_from_recall`
11. `test_embedding_dim_mismatch_detected`
12. `test_concurrent_appends_no_id_collision`
13. `test_fired_agent_memory_excluded`

## Error classes

```python
class MemoryError(Exception): ...
class MemoryPermissionDenied(MemoryError): ...
class EmbeddingDimensionMismatch(MemoryError): ...
class MemoryTooLarge(MemoryError): ...
```

## Definition of Done

- [ ] 3 layers implemented
- [ ] sqlite-vss installed, vector search working
- [ ] LocalEmbedder default, FakeEmbedder for tests
- [ ] Manager-subordinate isolation tested
- [ ] 13 tests passing
- [ ] Coverage ≥ 85%

## File skeleton

```
modules/memory/
├── PLAN.md
├── __init__.py
├── memory.py
├── working.py
├── episodic.py
├── semantic.py
├── embedders/
│   ├── __init__.py
│   ├── local.py        # sentence-transformers
│   └── fake.py         # deterministic
├── exceptions.py
├── migrations/001_init.sql
└── tests/
    ├── test_working.py
    ├── test_episodic.py
    ├── test_semantic.py
    ├── test_recall.py
    └── test_perms.py
```
