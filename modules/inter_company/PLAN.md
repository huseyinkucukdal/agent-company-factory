# Module 16 — Inter-Company Communication

## Purpose

Communication between companies connected to the same board. A "link" is established with Board approval. Vendor/customer relationships can be simulated. An agent from Company A sends a message to Company B via the Connector (B receives it internally as an external request).

## Responsibility boundaries

**Inside:**
- Link request and approval
- Cross-company message routing (Connector adapter)
- Link state machine: `requested → approved | denied`, `approved → suspended | revoked`
- Bilateral agreements (both companies' Boards must approve — since there is a single board in this project, one approval suffices)
- Vendor/customer role label (optional metadata)
- Link scope: which tools/message types are permitted (e.g. email-like only, API stub only)

**Outside:**
- Intra-company messaging (Orchestrator)
- External (off-board) communication (Connector)

## Dependencies

| Module | How |
|---|---|
| Approvals (board-level) | link approval |
| Connector | each company sees this as an "internal-link service" on top of Connector |
| Event Store | LINK_REQUESTED, LINK_APPROVED |
| Factory | which companies are active |

## Public API

```python
class InterCompanyService:
    def __init__(self, board_db, factory, approvals): ...

    async def request_link(self, *,
                           from_company: str,
                           to_company: str,
                           requested_by_user: str,
                           scope: LinkScope) -> Link: ...

    async def decide_link(self, link_id: str, decision: Decision,
                          decided_by_user: str) -> Link: ...

    async def revoke(self, link_id: str, by: str, reason: str) -> None: ...
    async def suspend(self, link_id: str, by: str, reason: str) -> None: ...
    async def resume(self, link_id: str, by: str) -> None: ...

    def list_for_company(self, company_id: str) -> list[Link]: ...

    async def send_cross(self, *,
                         link_id: str,
                         from_agent: str,
                         from_company: str,
                         to_company: str,
                         payload: CrossMessage) -> CrossResult:
        """Connector is used in the background."""

@dataclass(frozen=True)
class Link:
    id: str
    from_company: str
    to_company: str
    scope: LinkScope
    relationship: Relationship       # PEER | VENDOR | CUSTOMER
    status: LinkStatus
    requested_at: datetime
    approved_at: datetime | None
    approved_by: str | None

@dataclass(frozen=True)
class LinkScope:
    allowed_messages: set[CrossMessageKind]
    rate_limit_per_hour: int = 100
    max_payload_bytes: int = 65536

class LinkStatus(str, Enum):
    REQUESTED  = "requested"
    APPROVED   = "approved"
    DENIED     = "denied"
    SUSPENDED  = "suspended"
    REVOKED    = "revoked"

class Relationship(str, Enum):
    PEER     = "peer"
    VENDOR   = "vendor"     # to_company is vendor of from_company
    CUSTOMER = "customer"

class CrossMessageKind(str, Enum):
    INQUIRY  = "inquiry"
    QUOTE    = "quote"
    ORDER    = "order"
    INVOICE  = "invoice"
    DELIVERY = "delivery"
    GENERIC  = "generic"

@dataclass(frozen=True)
class CrossMessage:
    kind: CrossMessageKind
    subject: str
    body: str
    metadata: dict
```

## Cross-message flow

```
agent A (in company X) calls:
    tool external_call(service="inter_company", action="send",
                       args={link_id, kind, subject, body})
    ↓
Connector (X) → InterCompanyService.send_cross
    - link_id valid + status APPROVED
    - scope check (kind in allowed_messages)
    - rate limit check (per link)
    - sanitize body
    - target_company orchestrator → system_send to a designated inbox agent
        (default: HR or a configured "external_relations" agent)
    ↓
Target company sees it as USER_REQUEST kind from "external"
    - HR routes within company
```

## Inbox agent (target side)

Each company should have an "external relations" contact. Default: HR. Configurable at bootstrap.

If the CEO creates an external relations role (e.g. Sales), that agent becomes the inbox.

## Approval payload

```json
{
  "from_company": "co_xx",
  "from_company_name": "Acme",
  "to_company": "co_yy",
  "to_company_name": "BetaCorp",
  "relationship": "vendor",
  "scope": {
    "allowed_messages": ["inquiry", "quote", "order"],
    "rate_limit_per_hour": 100,
    "max_payload_bytes": 65536
  },
  "requester_user": "u_admin"
}
```

Approver: admin (board level). Since there is a single board, one approval is sufficient; with a multi-tenant board, both companies' admins would approve.

## Persistence (board DB)

```sql
CREATE TABLE inter_company_links (
    id              TEXT PRIMARY KEY,
    from_company    TEXT NOT NULL,
    to_company      TEXT NOT NULL,
    relationship    TEXT NOT NULL,
    scope_json      TEXT NOT NULL,
    status          TEXT NOT NULL,
    requested_by    TEXT NOT NULL,
    requested_at    TEXT NOT NULL,
    decided_by      TEXT,
    decided_at      TEXT,
    suspended_reason TEXT
);
CREATE INDEX idx_links_from ON inter_company_links(from_company, status);
CREATE INDEX idx_links_to   ON inter_company_links(to_company, status);
```

Cross-messages are written as `EXTERNAL_CALL` events to each company's Event Store (source: `inter_company:<link_id>`).

## Edge cases

| Scenario | Behaviour |
|---|---|
| from/to is the same company | Rejected |
| Company closed | Auto-revoke |
| Message sent after link is revoked | DENIED + notify agent |
| Link suspended | Message rejected; resumes when link is resumed |
| Second link request for the same pair | Rejected if active/pending exists |
| Rate limit exceeded | DENIED |
| max_payload exceeded | DENIED |
| Target company paused | Message queued or rejected? To be clarified with user — default: queue, delivered when target resumes |
| Target inbox agent fired | HR fallback; falls back to CEO if HR is also absent |

## Test scenarios

1. `test_request_link_creates_pending_approval`
2. `test_decide_link_approved_state_transition`
3. `test_decide_link_denied`
4. `test_send_before_approval_rejected`
5. `test_send_after_approval_routes_to_target`
6. `test_send_with_disallowed_kind_rejected`
7. `test_rate_limit_per_hour_enforced`
8. `test_payload_size_limit`
9. `test_revoke_link_blocks_future_sends`
10. `test_suspend_resume_link`
11. `test_company_close_auto_revokes_links`
12. `test_target_paused_queues_message`
13. `test_inbox_routes_to_hr_default`

## Error classes

```python
class InterCompanyError(Exception): ...
class LinkNotFound(InterCompanyError): ...
class LinkNotApproved(InterCompanyError): ...
class ScopeViolation(InterCompanyError): ...
class CrossRateLimited(InterCompanyError): ...
```

## Definition of Done

- [ ] Link CRUD + state machine tested
- [ ] Cross-message routing (Connector adapter) tested
- [ ] Auto-revoke when company closed
- [ ] Rate limit + scope check working
- [ ] Approval integration (board)
- [ ] Coverage ≥ 85%

## File skeleton

```
modules/inter_company/
├── PLAN.md
├── __init__.py
├── service.py
├── models.py
├── connector_adapter.py     # "service" registered with Connector
├── exceptions.py
├── migrations/board_003_links.sql
└── tests/
    ├── test_link_lifecycle.py
    ├── test_send.py
    ├── test_scope.py
    └── test_revoke.py
```
