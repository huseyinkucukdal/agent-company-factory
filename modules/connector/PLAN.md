# Module 09 — Connector

## Purpose

The company's single-gateway to the outside world. HTTP, email, payments, hosting, social ads, domains — all pass through here. The allowlist is managed by the Board; every external call incurs a Cost; risky ones go through Approval. Responses are sanitised (prompt injection defence).

## Responsibility boundaries

**Inside scope:**
- Service registry: `name`, `auth provider`, `cost calculator`, `risk level`, `allowlist gate`
- Allowlist enforcement (synced from board settings)
- Secret management (API keys are never visible to agents)
- Pre-flight cost + approval routing
- Response sanitization
- Rate limiting per service (e.g. SendGrid 100/hour)
- Retry policy (for idempotent calls)

**Outside scope:**
- The tool layer itself (Tools module)
- The approval queue (Approvals)
- Inter-company routing (Inter-Company module)

## Dependencies

| Module | How |
|---|---|
| Cost | cost calculation |
| Approvals | external_action approvals |
| Event Store | EXTERNAL_CALL event |
| Identity | agent identity (audit) |
| Tools | the tool layer uses Connector for `external_call` |

## Public API

```python
class Connector:
    def __init__(self, cost, approvals, events, secrets, allowlist): ...

    def register_service(self, service: ServiceDef) -> None: ...

    def is_allowed(self, service_name: str, action: str) -> bool: ...

    async def call(self,
                   *,
                   agent_id: str,
                   service: str,
                   action: str,
                   args: dict,
                   correlation_id: str) -> ConnectorResult: ...

@dataclass(frozen=True)
class ServiceDef:
    name: str
    description: str
    auth: AuthMethod                # NONE | API_KEY | OAUTH
    risk: RiskLevel                 # LOW | MEDIUM | HIGH | CRITICAL
    actions: dict[str, ActionDef]   # action_name → schema, executor, cost

@dataclass(frozen=True)
class ActionDef:
    description: str
    args_schema: type[BaseModel]
    output_schema: type[BaseModel]
    cost_estimator: Callable[[dict], Money]
    rate_limit: RateLimit | None
    sanitizer: Callable[[Any], BaseModel]
    auto_approve_threshold: Money | None
            # None → always requires approval; Money → auto-approved if below this threshold
class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"   # always requires approval, never auto

@dataclass(frozen=True)
class ConnectorResult:
    ok: bool
    output: BaseModel | None
    error: str | None
    cost_usd: Decimal
    approval_request_id: str | None  # if pending
```

## Auto-approval rules

| Risk | Behaviour |
|---|---|
| LOW + cost ≤ threshold | Auto-approve (e.g. GET requests under $0.10) |
| MEDIUM | Approval required (Security pre-veto + Board) |
| HIGH | Approval required |
| CRITICAL | Approval required, with Security DUAL (extra Security agent approval) |

Each company can set its own auto-approve threshold from the Board; the upper limit is in config.

## Sanitization (critical)

Responses from the outside world do not flow directly to the agent. Service.action.sanitizer is called:

```python
def sanitize_http_response(raw: httpx.Response) -> HttpResponse:
    return HttpResponse(
        status=raw.status_code,
        headers=safe_headers(raw.headers),
        body_preview=raw.text[:2000],   # truncate
        body_length=len(raw.text),
        # raw HTML/JSON returned to agent in parsed form
    )
```

All output returned to the agent is a `BaseModel` — JSON-schema'd and structured. Even free-form text is in at least a `text: str` field — `<system>` tags are filtered out, and "instruction"/"system" keys inside JSON are flagged.

## Secret management

```python
class Secrets(Protocol):
    def get(self, service: str, key: str) -> str: ...
    def set(self, service: str, key: str, value: str) -> None: ...
```

Default implementation: encrypted SQLite table (sodium/libnacl symmetric). Swappable to a dedicated secret manager (e.g. Doppler, Vault) in production.

Agents never see secrets — the Connector executor resolves them during the service call.

## Services (initial set)

| Service | Actions | Risk |
|---|---|---|
| `http` | `get`, `post`, `put`, `delete` | MEDIUM (by URL) |
| `email` | `send`, `send_with_attachment` | MEDIUM |
| `domain` | `register`, `renew`, `transfer` | HIGH |
| `hosting` | `create_droplet`, `destroy_droplet`, `list` | HIGH |
| `payment` | `charge_customer`, `refund` | CRITICAL |
| `ads` | `create_campaign`, `pause`, `set_budget` | HIGH |
| `social` | `post`, `delete_post`, `get_metrics` | MEDIUM |

In the first implementation, only `http` and `email` (mock provider). The others remain defined as stubs.

## Rate limiting

Per-service token bucket. If exceeded, `RateLimited` is returned; the agent is informed to "wait n seconds".

## Idempotency

A duplicate call with the same `correlation_id`:
- If the previous call was APPROVED and OK: returns cached result
- If previous was PENDING: returns the same approval_request_id
- If previous was FAILED: a new call is made

## Retry policy

For idempotent actions (`http.get`, `email.send` with idempotency-key), network errors trigger 3 retries with exponential backoff. No retry for non-idempotent actions.

## Edge cases

| Scenario | Behaviour |
|---|---|
| Service not on allowlist | DENIED("not_allowed") + event |
| API key missing | DENIED("missing_credential") |
| Sanitization failed (raw response cannot be parsed) | DENIED("malformed_response") + raw bytes archive (for debugging) |
| Response is 100MB | Truncate + warning |
| Auth provider down | RETRY + eventually DENIED |
| Same endpoint accessed by different agents simultaneously | Rate limit is per service, not per agent |
| Risk level ambiguous | Default HIGH (safe side) |

## Test scenarios

1. `test_call_disallowed_service_denied`
2. `test_call_missing_credential_denied`
3. `test_low_risk_under_threshold_auto_approved`
4. `test_medium_risk_routes_to_approval`
5. `test_critical_risk_dual_security_approval`
6. `test_pending_approval_blocks_call`
7. `test_approved_call_proceeds_and_charges`
8. `test_denied_approval_no_charge`
9. `test_sanitizer_strips_dangerous_fields`
10. `test_idempotent_correlation_returns_cached`
11. `test_rate_limit_enforced_per_service`
12. `test_retry_on_network_error`
13. `test_secret_never_in_event_payload`
14. `test_response_truncated_above_limit`
15. `test_event_emitted_with_cost`

## Error classes

```python
class ConnectorError(Exception): ...
class ServiceNotAllowed(ConnectorError): ...
class MissingCredential(ConnectorError): ...
class MalformedResponse(ConnectorError): ...
class RateLimited(ConnectorError): ...
class ExternalServiceFailure(ConnectorError): ...
```

## Definition of Done

- [ ] Service registry + auto-approve thresholds
- [ ] HTTP and email services (mock provider) working
- [ ] Sanitization layer tested
- [ ] Secret store (encrypted SQLite) integrated
- [ ] Rate limit tested
- [ ] Idempotency tested
- [ ] Coverage ≥ 85%

## File skeleton

```
modules/connector/
├── PLAN.md
├── __init__.py
├── connector.py
├── allowlist.py
├── secrets.py
├── sanitizer.py
├── rate_limit.py
├── services/
│   ├── __init__.py
│   ├── http.py
│   ├── email.py
│   ├── domain.py        # stub
│   ├── hosting.py       # stub
│   ├── payment.py       # stub
│   ├── ads.py           # stub
│   └── social.py        # stub
├── exceptions.py
└── tests/
    ├── test_call.py
    ├── test_sanitizer.py
    ├── test_secrets.py
    ├── test_rate_limit.py
    └── test_services.py
```
