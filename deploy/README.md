# Deploy

Container-based deployment recipe for the integrated runtime.

## Quick start

```bash
cd deploy
cp .env.example .env
docker compose up --build
```

The integrated runtime entrypoint is:

```
python -m modules.integration serve --host 0.0.0.0 --port 8000
```

It wires the Board API, Company Factory, and Inter-Company service against
a single SQLite-backed data directory mounted at `/data` inside the
container.

## CLI

The same image exposes the operator CLI:

```bash
docker compose run --rm app python -m modules.integration <subcommand>
```

See `python -m modules.integration --help`.
