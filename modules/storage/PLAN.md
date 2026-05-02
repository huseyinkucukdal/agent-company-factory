# Module 01 — Storage Layer

## Purpose

Per-company isolated DB (SQLite) and per-agent workspace (FS) management. Disk quota enforcement. Resolve all file access through IDs, make path traversal impossible.

## Responsibility boundaries

**In scope:**
- Opening, closing, and schema migration of company DB files
- Creating and deleting workspace directories
- Quota calculation (cache + actual filesystem cross-check)
- Path resolution: `(agent_id, relative_path)` → safe absolute path
- Read/write permission checks (via org chart — but the Identity module provides this; Storage receives the `Identity` interface via constructor injection)
- Write rejection when quota is exceeded
- Connection pooling (SQLite per-company connection cache)

**Out of scope:**
- Knowing who manages which agent (Identity provides this)
- Writing events (delegated to Event Store; but important storage events—e.g. `quota_warning`—are pushed to Event Store)
- Pricing or cost calculation

## Dependencies

| Module | How it is used | Mock interface |
|---|---|---|
| Identity | `can_read_workspace(reader_id, owner_id) → bool` | `IdentityProvider(Protocol)` |
| Event Store | quota warning/exceeded events | `EventSink(Protocol)` |

During testing, both are injected with fake implementations (`tests/fakes.py`).

## Public API

```python
# modules/storage/db.py
class CompanyDB:
    @staticmethod
    def init(company_id: str, root: Path) -> "CompanyDB": ...
    def connect(self) -> sqlite3.Connection: ...  # connection-per-thread
    def migrate(self) -> None: ...
    def close(self) -> None: ...
    def archive(self, dest: Path) -> None: ...  # when the company closes

class BoardDB:
    @staticmethod
    def init(root: Path) -> "BoardDB": ...
    def connect(self) -> sqlite3.Connection: ...
    def migrate(self) -> None: ...

# modules/storage/workspace.py
class Workspace:
    def __init__(self, company_id: str, root: Path,
                 identity: IdentityProvider, events: EventSink): ...

    def create_for_agent(self, agent_id: str, quota_mb: int) -> None: ...
    def delete_for_agent(self, agent_id: str) -> None: ...

    def write(self, agent_id: str, relative_path: str, data: bytes) -> WriteResult: ...
    def read_own(self, agent_id: str, relative_path: str) -> bytes: ...
    def read_subordinate(self, reader_id: str, owner_id: str,
                         relative_path: str) -> bytes: ...
    def list(self, agent_id: str, relative_path: str = "") -> list[FileMeta]: ...
    def delete(self, agent_id: str, relative_path: str) -> None: ...

# modules/storage/quota.py
class Quota:
    def usage(self, scope: QuotaScope) -> int:  # bytes
    def limit(self, scope: QuotaScope) -> int:
    def set_limit(self, scope: QuotaScope, mb: int) -> None:
    def remaining(self, scope: QuotaScope) -> int:

QuotaScope = Company(company_id) | Agent(agent_id)

@dataclass(frozen=True)
class WriteResult:
    bytes_written: int
    company_remaining_mb: float
    agent_remaining_mb: float

class QuotaExceeded(StorageError): ...
class PermissionDenied(StorageError): ...
class PathOutsideWorkspace(StorageError): ...
```

## Persistence

**Board DB (`data/board.db`):**
```sql
-- this module owns no tables — it only creates the DB skeleton
-- other modules add their own tables via migration
CREATE TABLE schema_version (module TEXT, version INTEGER, PRIMARY KEY (module));
```

**Per-company DB:**
```sql
CREATE TABLE schema_version (module TEXT, version INTEGER, PRIMARY KEY (module));

CREATE TABLE workspaces (
    agent_id    TEXT PRIMARY KEY,
    quota_bytes INTEGER NOT NULL,
    used_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
-- used_bytes is a cache; the actual filesystem is reconciled every minute (windowed job)
```

**FS layout:**
```
data/companies/<company_id>/
    company.db
    workspaces/<agent_id>/...   # free file tree
    archive/                    # moved here when the company closes
```

## Path resolution algorithm

```
def resolve(agent_id, relative_path):
    base = root / "companies" / company_id / "workspaces" / agent_id
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base.resolve()):
        raise PathOutsideWorkspace
    return target
```

All attempts with an empty `relative_path`, containing `..`, using an absolute path, or escaping via a symlink are rejected. Creating symlinks is also forbidden (enforced in the write function).

## Quota enforcement

- Pre-write: raise `QuotaExceeded` if `current_used + len(data) > limit`
- Two levels: both the agent quota and the company total quota are checked
- To prevent race conditions in concurrent writes, use a DB-level row lock (`UPDATE workspaces SET used_bytes = ... WHERE agent_id = ?`) — within a transaction
- Reconcile job: compare the actual FS size with the cache every minute; emit a `quota_drift` event if there is a discrepancy

## Edge cases

| Scenario | Behavior |
|---|---|
| Agent deleted, workspace still exists | Aggressive deletion on `delete_for_agent` call; remainder handled by GC |
| Company DB file corrupted | WAL replay on open; if it fails, a restore from the last archive is triggered (manual — Factory's responsibility) |
| Agent A tries to read B's workspace; Identity reports reader/owner relationship as "not a manager" | `PermissionDenied` |
| If quota is reduced to `0 MB` | New writes are rejected; existing files remain |
| Very large file (100MB) and quota 200MB | Can be written in one go; but rejected if the write size exceeds the remaining quota |
| Two agents trying to write to the same path | Impossible — the path is always under the owner's directory |

## Test scenarios

Unit tests (`tests/test_*.py`):

1. `test_db_init_creates_schema_version_table`
2. `test_migrate_idempotent` — calling twice causes no harm
3. `test_workspace_write_under_quota_succeeds`
4. `test_workspace_write_exceeds_agent_quota_raises`
5. `test_workspace_write_exceeds_company_quota_raises`
6. `test_path_traversal_dotdot_blocked`
7. `test_path_traversal_absolute_blocked`
8. `test_path_traversal_symlink_blocked` — symlink points outside
9. `test_read_own_workspace_succeeds`
10. `test_read_subordinate_when_manager_succeeds` — fake identity returns true
11. `test_read_subordinate_when_not_manager_denied`
12. `test_concurrent_writes_quota_consistent` — threading test
13. `test_quota_reconcile_detects_drift`
14. `test_archive_moves_company_dir`
15. `test_delete_agent_removes_files_and_row`

Property-based test (hypothesis):
- Random path string → must either be a valid relative path or raise the appropriate exception (never a silent success or a file outside the FS)

## Error classes

```python
class StorageError(Exception): ...
class QuotaExceeded(StorageError): ...
class PermissionDenied(StorageError): ...
class PathOutsideWorkspace(StorageError): ...
class CompanyNotFound(StorageError): ...
class CorruptDatabase(StorageError): ...
```

## Definition of Done

- [ ] All public APIs listed above are implemented
- [ ] All 15 unit tests pass + property test
- [ ] Coverage ≥ 85%
- [ ] mypy strict clean
- [ ] ruff clean
- [ ] `IdentityProvider` and `EventSink` Protocol defined, with fakes in `tests/fakes.py`
- [ ] Reconcile job documented (cron scheduling is Factory/Orchestrator's responsibility — but the function lives here)
- [ ] README with module heading + 1 example usage snippet
- [ ] Migration system works via `schema_version`

## File skeleton (to be created during implementation)

```
modules/storage/
├── PLAN.md
├── __init__.py            # public API exports
├── db.py                  # CompanyDB, BoardDB
├── workspace.py           # Workspace
├── quota.py               # Quota
├── exceptions.py
├── protocols.py           # IdentityProvider, EventSink
├── migrations/
│   ├── board/001_init.sql
│   └── company/001_init.sql
└── tests/
    ├── conftest.py
    ├── fakes.py
    ├── test_db.py
    ├── test_workspace.py
    ├── test_quota.py
    └── test_paths.py
```

## Open questions

- Migration runner: custom mini runner or alembic? Recommendation: custom mini runner (SQLite + single table, alembic is overkill).
- Backup format: plain SQLite file copy or `VACUUM INTO`? Recommendation: `VACUUM INTO` is safer.
