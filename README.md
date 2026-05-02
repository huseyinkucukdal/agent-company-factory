# AI Company Factory

Platform for spawning and operating autonomous AI-agent companies. See [MASTER_PLAN.md](MASTER_PLAN.md) for the design and module roadmap.

## Quick start (Docker only)

This project runs entirely inside Docker. The host machine does not need Python.

```bash
make build         # build the image
make test          # run the full test suite
make test-storage  # run only the Storage module tests
make shell         # bash inside the container
make lint
make typecheck
```

## Layout

- `MASTER_PLAN.md` — top-level design and module list
- `modules/<name>/PLAN.md` — per-module detailed plan
- `modules/<name>/` — Python implementation
- `data/` — runtime artifacts (DBs, workspaces); persisted as a docker volume

## Module 01 — Storage

Per-company SQLite + per-agent workspace filesystem with two-level disk
quota and ID-based path resolution. Example usage:

```python
from pathlib import Path
from modules.storage import (
    CompanyDB, CompanyQuota, Quota, Workspace,
)

root = Path("/app/data")
db = CompanyDB.init("acme", root); db.migrate()
Quota(db).set_limit(CompanyQuota(), mb=100)

ws = Workspace(db, identity=my_identity, events=my_events)
ws.create_for_agent("ceo", quota_mb=10)
ws.write("ceo", "notes/day1.md", b"# kickoff")
data = ws.read_own("ceo", "notes/day1.md")
```

Run only this module's tests: `make test-storage`.
