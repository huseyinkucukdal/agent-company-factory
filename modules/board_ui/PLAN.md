# Module 15 — Board Frontend (Next.js)

## Purpose

Interface for the Board user to perform live monitoring, approve requests, and configure settings. Live feed (SSE), org chart, approval inbox, replay slider, settings.

## Technology

- **Next.js 14+ (App Router)**
- **TypeScript**
- **Tailwind CSS** + **shadcn/ui** components
- **TanStack Query** (server state)
- **Zustand** (client state — minimal)
- **react-hook-form** + **zod** (forms)
- **EventSource** (SSE)
- **React Flow** (org chart)
- **Recharts** (budget/expense charts)

## Page map

```
/login
/register                       # first admin registration
/                               # dashboard: companies grid
/companies/new                  # CompanySpec form
/companies/[id]                 # company detail
    ├─ /                         # overview (live feed + org chart)
    ├─ /approvals                # pending approvals for this company
    ├─ /agents                   # list, agent detail
    ├─ /agents/[agentId]         # workspace, memory, messages
    ├─ /budget                   # state + charts + expense list
    ├─ /settings                 # budget, disk, allowlist, auto-approve
    ├─ /replay                   # event timeline slider
    └─ /links                    # inter-company links for this company
/approvals                       # all pending approvals (cross-company)
/settings/users                 # admin: user management
/settings/audit                 # board audit log
```

## Key features

### Live feed
- Connect to SSE stream: `/api/companies/{id}/stream?since=<lastEventId>`
- Events in a virtualised list sorted by date (`@tanstack/react-virtual`)
- Filter: kind, agent, correlation
- Colour-coded: messages (blue), tools (green), expense (yellow), security (red)
- Click a message row to open thread view
- Auto-scroll ON/OFF toggle

### Org chart
- Hierarchical tree with React Flow
- Each node: avatar, role, status (idle/working/blocked/unhealthy)
- Status colour-coded (green/yellow/orange/red)
- Click to open agent detail slide-in
- "Fire" button (for authorised users)
- "Hire request" button (triggers an HR task)

### Approval inbox
- Card list: kind icon, requester, payload preview, expires_at countdown
- Decision modal: approve / reject + note
- Bulk decide (for items of the same type)
- Filter: kind, company, age
- Real-time update (SSE or 10s polling)

### Replay
- Event timeline (time bar, day by day)
- Navigate to past time with slider
- "Now" → "X hours ago" toggles
- Replayed state is read-only — action buttons disabled

### Budget
- Total, spent, remaining (large metric cards)
- Pie chart by category
- Time series (daily spending)
- Subscription list
- "Increase budget" button → BUDGET_INCREASE approval (auto-approve for admin)

### Settings
- Form: budget, disk quota (total + per-agent default)
- Allowlist editor: allowed actions checkbox per service
- Auto-approve thresholds: per-service threshold
- Inter-company links toggle and approved list
- "Pause / resume / close company"

### Agent detail
- Persona prompt visible (read-only)
- Workspace file explorer (for manager or admin)
- Memory: episodic + semantic list (for manager or admin)
- Message history (filtered event store)
- Health signals
- Tool usage statistics

## Auth flow

- `/login` POST → access + refresh token
- Access token: not httpOnly cookie — memory + localStorage (short-lived to mitigate XSS risk)
- Refresh token: httpOnly cookie (SameSite=strict for CSRF)
- 401 → automatic refresh → redirect to login on failure

## State management

- TanStack Query: all server state (companies, approvals, paginated events)
- Zustand: UI preferences (filters, dark mode, sidebar open/closed)
- SSE events: `useEventStream` custom hook → invalidate or append query cache

## SSE hook

```typescript
function useEventStream(companyId: string, since?: number) {
  const [events, setEvents] = useState<Event[]>([]);
  useEffect(() => {
    const es = new EventSource(`/api/companies/${companyId}/stream?since=${since ?? 0}`);
    es.onmessage = (m) => setEvents((evs) => [...evs, JSON.parse(m.data)]);
    es.onerror = () => { /* reconnect with backoff */ };
    return () => es.close();
  }, [companyId, since]);
  return events;
}
```

Reconnect: exponential backoff (1s, 2s, 4s, max 30s); remembers last event_id and resumes with `since`.

## i18n

First version: Turkish + English. `next-intl` or a simple JSON dictionary. UI strings accessed via `t('...')`.

## Accessibility

- Basic WCAG AA compliance
- All interactive elements keyboard accessible
- Approve/Deny buttons ARIA labelled
- Dark/light mode

## Performance

- Live feed virtualised (1000+ entries)
- Org chart virtualised layout for 100+ agents
- Code splitting: replay page lazy-loaded
- React Server Components: data fetch pages

## Test strategy

- **Unit**: utility functions (`vitest`)
- **Integration**: hooks (`react-testing-library`)
- **E2E**: critical flows (`playwright`)
  - Login → create company → first message
  - Give approval → decide → state updates
  - Pause → resume

## Test scenarios

1. `Login screen requires email + password`
2. `Successful login redirects to /`
3. `Invalid token shows session-expired and redirects login`
4. `Create company submits CompanySpec`
5. `Live feed renders incoming SSE events`
6. `Live feed reconnects on disconnect`
7. `Approval decide updates list optimistically`
8. `Org chart shows status colors`
9. `Workspace viewer denied for observer`
10. `Replay slider scrubs events`
11. `Budget settings update reflects in detail page`
12. `Auto-approve threshold form validates positive`
13. `Inter-company link request UI`
14. `Audit page admin-only`

## Definition of Done

- [ ] All pages implemented
- [ ] SSE live feed stable (reconnect tested for 5+ minutes)
- [ ] Approve/Deny round-trip < 1 sec (UX)
- [ ] Org chart smooth with 50+ agents
- [ ] E2E happy paths passing
- [ ] Lighthouse perf score > 80
- [ ] Theme (light/dark) working
- [ ] Turkish + English i18n

## File skeleton

```
modules/board_ui/
├── PLAN.md
├── package.json
├── next.config.mjs
├── tailwind.config.ts
├── tsconfig.json
├── app/
│   ├── layout.tsx
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   └── register/page.tsx
│   ├── (dash)/
│   │   ├── page.tsx                 # companies grid
│   │   ├── companies/
│   │   │   ├── new/page.tsx
│   │   │   └── [id]/
│   │   │       ├── page.tsx
│   │   │       ├── approvals/page.tsx
│   │   │       ├── agents/page.tsx
│   │   │       ├── agents/[agentId]/page.tsx
│   │   │       ├── budget/page.tsx
│   │   │       ├── settings/page.tsx
│   │   │       ├── replay/page.tsx
│   │   │       └── links/page.tsx
│   │   ├── approvals/page.tsx
│   │   └── settings/
│   │       ├── users/page.tsx
│   │       └── audit/page.tsx
├── components/
│   ├── ui/                  # shadcn primitives
│   ├── live-feed.tsx
│   ├── org-chart.tsx
│   ├── approval-card.tsx
│   ├── replay-slider.tsx
│   ├── budget-charts.tsx
│   └── ...
├── lib/
│   ├── api.ts               # fetcher + auth
│   ├── sse.ts
│   ├── auth-store.ts
│   └── i18n.ts
├── hooks/
│   └── ...
├── messages/
│   ├── tr.json
│   └── en.json
└── e2e/
    ├── login.spec.ts
    ├── company.spec.ts
    └── approvals.spec.ts
```

---

## Round 1 deltas (applied)

The scope of the first round was clarified with the user and the following were completed:

**Added**

- Dependencies: `@radix-ui/react-{dialog,popover,select,tabs,toast}`, `@hookform/resolvers`, `react-hook-form`, `reactflow`, `@tanstack/react-virtual`.
- UI primitives (`components/ui/{dialog,popover,select,tabs,toast}.tsx`) — written by hand with bare Tailwind + Radix, not shadcn.
- Toast infrastructure: `ToastProvider` connected globally to `app/providers.tsx`, `useToast()` hook (success/error helpers).
- Company settings page (`app/(dash)/companies/[id]/settings/page.tsx`):
  - Lifecycle card (pause / resume / close) — admin-only, confirm required for close.
  - Tabs: **Budget** (total USD, zod validation), **Allowlist** (service/action checkbox table), **Thresholds** (per-action auto-approve threshold), **Disk quota** (company + per-agent).
  - operator/admin can edit, observer is read-only.
- Org chart (`components/org-chart.tsx`): React Flow + recursive layout algorithm; clicking a node opens a Dialog with details + link to agent page.
- Live feed (`components/live-feed.tsx`): virtualised stream with TanStack Virtual, kind/agent/correlation filters, auto-scroll (pin detection), click a row to open thread Dialog by correlation_id, kind-based colour coding.
- Company overview page (`app/(dash)/companies/[id]/page.tsx`) reorganised: lifecycle buttons moved to settings; main grid is live feed (3 cols) + org chart (2 cols).

**Removed / cleaned up**

- Pages router stubs (`pages/_app.tsx`, `pages/_document.tsx`, `pages/_error.tsx`) deleted; minimal stubs added back for the Next 14 build (only to generate 404/500 fallbacks, contains no user code).

**Validation**

- `npm run typecheck` ✓
- `npm run lint` ✓
- `npm run build` ✓ (11/11 pages, inside container)

**Intentionally deferred (to later rounds)**

- Budget/expense charts with Recharts.
- Replay timeline slider.
- Cross-company link request UI (API only, no UI yet).
- Dark-mode toggle (CSS variables ready, switch not yet implemented).
- i18n (tr/en) — currently hardcoded in Turkish.
- Agent memory panel and workspace file explorer.
- Playwright E2E tests.
- SSE auth hardening (token cookie / EventSource auth header).
