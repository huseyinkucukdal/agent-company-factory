# Module 15 — Board Frontend (Next.js)

Next.js 14 (App Router) + TypeScript + Tailwind. Consumption point: Board API
(`modules/board_api`). For UI development, the Board API must be up before
this module's container (provided by the `api` service in docker-compose).

## Running

All commands from the repo root:

```bash
docker compose up ui                  # API + UI together (http://localhost:3000)
docker compose run --rm ui npm run typecheck
docker compose run --rm ui npm run lint
docker compose run --rm ui npm run build
```

On first launch, create an admin account via `Register` (the first registration
opens with admin privileges). Tokens are stored in `localStorage` under the
`acf.board.auth` key; the access token is automatically refreshed on expiry.

## Implemented in this pass

- Auth: login + register pages, automatic refresh flow
- Dashboard: company grid + new company form
- Company detail: live SSE feed, team card, pause/resume/close buttons
- Approvals: global and per-company inbox + approve/reject
- Team: agent list + agent detail + workspace file list
- Budget: summary cards + category breakdown
- Replay: kind-filtered event list
- Links: inter-company connections (read-only)
- Admin: user list, audit log

## Still skeleton only

- Org chart (React Flow)
- Budget charts (Recharts)
- Replay slider (time scrubber)
- Settings forms (allowlist editor, threshold per-service)
- i18n (language hardcoded to EN)
- E2E (Playwright) tests
- Dark mode toggle

These are on the roadmap in `PLAN.md` and will be filled in subsequent passes.

## Architecture notes

- **API proxy**: `next.config.mjs` routes `/api/*` requests to `ACF_API_BASE`
  (default `http://api:8000`) server-side; no browser CORS.
- **SSE**: Because `EventSource` cannot carry custom headers, the JWT is
  currently sent in the query string (`?token=`). If Board API enforces
  `Authorization`, the feed page may receive a 401 in this pass. For
  production, prefer cookie-based SSE auth or a short-lived stream-token
  endpoint.
- **State**: TanStack Query for server cache; Zustand only for the auth
  session. SSE events accumulate in component-local state.
