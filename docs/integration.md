# Module 17 — Integration & E2E

This module wires every other module into a single runtime, exposes an
operator CLI (`python -m modules.integration`), and ships a thin
deployment recipe under `deploy/`.

## Public surface

```python
from modules.integration import (
    IntegratedRuntime,
    bootstrap_runtime,
    build_post_create_hook,
    migrate_all,
    register_inter_company_for_handle,
)
```

- `bootstrap_runtime(*, data_dir, llm_factory, factory_config_overrides=None)`
  initialises the board database, runs every migration, constructs the
  `CompanyFactory`, the `LinkService`, and the `InterCompanyService`,
  installs a `post_create` hook on the factory so newly created or
  restarted companies get the inter-company connector service
  registered, and finally returns an `IntegratedRuntime` containing a
  ready FastAPI app.
- `migrate_all(*, board_db, factory=None, inter_company=None)` is the
  single entry point that runs storage + board_api + factory +
  inter_company migrations in order. Each migration is idempotent.
- `IntegratedRuntime.close()` shuts down the factory and the board DB.

## CLI

```
python -m modules.integration init                 # create data dir + board.db
python -m modules.integration db migrate           # run all migrations
python -m modules.integration serve                # run the FastAPI app
python -m modules.integration company list
python -m modules.integration company create --spec spec.json --by USER
python -m modules.integration company describe <id>
python -m modules.integration company close <id> --by USER
python -m modules.integration approvals pending
python -m modules.integration events tail <company-id> [--limit N]
```

## Tests

`modules/integration/tests/` covers:

- runtime bootstrap + factory hook (`test_e2e_create_company.py`)
- end-to-end inter-company link request → approval → cross-send
  (`test_e2e_inter_company.py`)
- pause + factory restart preserves clock state and re-registers the
  inter-company service (`test_e2e_pause_resume.py`)
- migration idempotency and table coverage (`test_migrations.py`)
- CLI surface smoke tests (`test_cli.py`)
