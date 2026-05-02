# Module 05 — Cost & Budget Engine

## Purpose

Reduces token + tool costs to a single currency and reconciles them against the company budget. Pre-flight estimation (cost calculation before a tool is called) + post-flight collection (actual cost). Monthly subscriptions (e.g. domain, server) are periodically charged via `DAY_TICK` events from the Clock.

## Responsibility boundaries

**In scope:**
- Pricing catalog (LLM token rates, tool fees, subscriptions)
- Functions that calculate cost estimates (each tool has its own formula)
- Budget calculation, billing, threshold warnings (warning, blocked)
- Monthly recurring subscriptions (domain `$12/yr`, server `$5/mo`)
- Category-based reporting (LLM, tools, hosting, ads, etc.)
- Currency: single base currency, USD default

**Out of scope:**
- Approval flow (Approvals)
- Tool registration mechanism (Tools)
- Money transfer/actual payment (Connector)

## Dependencies

| Module | How |
|---|---|
| Storage | `expenses`, `pricing_catalog`, `subscriptions` tables |
| Event Store | `EXPENSE_CHARGED`, `BUDGET_WARNING`, `BUDGET_BLOCKED` |
| Clock | Listens for `DAY_TICK` — subscription billing |

## Public API

```python
class Pricing:
    def __init__(self, db: CompanyDB): ...

    def register_tool(self, name: str,
                      estimator: CostEstimator,
                      category: Category) -> None: ...

    def estimate(self, tool_name: str, args: dict) -> Money:
        """Tool call cost (deterministic or worst-case estimate)."""

    def estimate_llm(self, model: str,
                     input_tokens: int, output_tokens_max: int) -> Money: ...

    def actual_llm(self, model: str, usage: TokenUsage) -> Money: ...

class Budget:
    def __init__(self, db: CompanyDB, events: EventStore, pricing: Pricing): ...

    def state(self) -> BudgetState: ...
    def set_total(self, amount_usd: float) -> None: ...

    def can_afford(self, amount: Money) -> bool: ...

    def charge(self, *, agent_id: str, amount: Money,
               category: Category, memo: str,
               approval_id: str | None = None) -> ChargeResult:
        """ChargeResult: OK | OVER_BUDGET (with details)
        Does not raise on budget overrun — the caller decides; but an event is written."""

    def reservations(self) -> list[Reservation]: ...
    def reserve(self, amount: Money, ref: str) -> Reservation:
        """Pre-flight: holds funds before an agent starts an action.
        Can be cancelled or committed."""
    def release(self, reservation_id: str) -> None: ...
    def commit(self, reservation_id: str, actual_amount: Money) -> None: ...

@dataclass(frozen=True)
class Money:
    amount_usd: Decimal           # 4 decimal places of precision

class Category(str, Enum):
    LLM        = "llm"
    TOOL       = "tool"
    HOSTING    = "hosting"
    DOMAIN     = "domain"
    EMAIL      = "email"
    ADS        = "ads"
    PAYMENT    = "payment"
    OTHER      = "other"

@dataclass(frozen=True)
class BudgetState:
    total: Money
    spent: Money
    reserved: Money
    remaining: Money
    by_category: dict[Category, Money]
    warning_threshold_pct: float = 80.0
    blocked: bool

class Subscription:
    """Monthly recurring."""
    id: str
    description: str
    amount_per_company_month: Money
    next_charge_company_day: int
    category: Category

class Subscriptions:
    def __init__(self, db: CompanyDB, budget: Budget, events: EventStore): ...
    def add(self, sub: Subscription) -> None: ...
    def remove(self, id: str) -> None: ...
    def on_day_tick(self, day: int) -> None:
        """Clock callback: charges due subscriptions."""
```

## Pricing catalog structure

The tool registrar calls `register_tool`; `estimator` is a callable:

```python
CostEstimator = Callable[[dict], Money]

# example: e-mail send tool
def email_estimator(args: dict) -> Money:
    n_recipients = len(args.get("to", []))
    return Money(amount_usd=Decimal("0.001") * n_recipients)
```

A separate table for LLM models (input/output cost per 1k tokens). These are loaded from a config file (`pricing.toml`).

```toml
[llm.claude-opus-4-7]
input_per_1k = 0.015
output_per_1k = 0.075

[llm.claude-sonnet-4-6]
input_per_1k = 0.003
output_per_1k = 0.015

[llm.claude-haiku-4-5]
input_per_1k = 0.0008
output_per_1k = 0.004
```

## Reservation pattern (critical)

The pre-flight cost estimate for a tool call may differ from the actual cost (LLM output token count). Solution: **reserve → execute → commit/release**.

```
1. agent calls the tool
2. Tools layer calculates the estimate (worst-case)
3. Budget.reserve(estimate) → if reserved+spent > total → reject
4. Execute the tool
5. actual cost is calculated
6. Budget.commit(reservation, actual) — transferred to spent
   or Budget.release(reservation) — cancelled
```

Reserved amounts appear in `BudgetState.reserved`; "available" remaining = total - spent - reserved.

## DAY_TICK consumption

The Cost engine listens to Clock directly:

```python
def on_day_tick(self, day):
    for sub in self.subscriptions.due_at(day):
        result = self.budget.charge(
            agent_id="<system>",
            amount=sub.amount_per_company_month,
            category=sub.category,
            memo=f"Subscription: {sub.description}",
        )
        if result.over_budget:
            events.append(BUDGET_BLOCKED, {...})
        sub.next_charge_company_day += 30  # every 30 company days
```

## Budget threshold warnings

Checked after `charge` and `reserve`:
```
spent_pct = (spent + reserved) / total * 100
if spent_pct >= 80 and not warned:
    events.append(BUDGET_WARNING, {pct: spent_pct})
if spent_pct >= 100:
    events.append(BUDGET_BLOCKED, {})
    self.blocked = True
```

While `blocked`, `charge` and `reserve` return `OVER_BUDGET`. Automatically unblocked when the Board increases the total.

## Persistence

```sql
CREATE TABLE pricing_catalog (
    name        TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    formula     TEXT NOT NULL  -- estimator reference (resolved on the code side)
);

CREATE TABLE subscriptions (
    id                          TEXT PRIMARY KEY,
    description                 TEXT NOT NULL,
    amount_usd                  REAL NOT NULL,
    category                    TEXT NOT NULL,
    next_charge_company_day     INTEGER NOT NULL,
    period_days                 INTEGER NOT NULL DEFAULT 30,
    cancelled                   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE expenses (
    id           TEXT PRIMARY KEY,
    agent_id     TEXT,
    amount_usd   REAL NOT NULL,
    category     TEXT NOT NULL,
    memo         TEXT,
    approval_id  TEXT,
    ts_company   TEXT NOT NULL,
    ts_real      TEXT NOT NULL
);

CREATE TABLE reservations (
    id           TEXT PRIMARY KEY,
    amount_usd   REAL NOT NULL,
    ref          TEXT NOT NULL,
    status       TEXT NOT NULL,  -- open | committed | released
    created_at   TEXT NOT NULL
);

CREATE TABLE budget_state (
    id INTEGER PRIMARY KEY CHECK (id=1),
    total_usd      REAL NOT NULL,
    blocked        INTEGER NOT NULL DEFAULT 0,
    last_warn_pct  REAL DEFAULT 0
);
```

## Decimal precision

Always use `Decimal`; use `float` only during DB serialization. All arithmetic at 4 decimal places of precision, using `quantize(Decimal("0.0001"))`.

## Test scenarios

1. `test_estimate_llm_input_output`
2. `test_actual_llm_uses_real_token_count`
3. `test_estimate_tool_via_registered_estimator`
4. `test_charge_within_budget_succeeds`
5. `test_charge_over_budget_returns_over_budget`
6. `test_reserve_release_does_not_consume`
7. `test_reserve_commit_actual_higher_than_estimate`
8. `test_reserve_commit_actual_lower_than_estimate`
9. `test_warning_event_at_80_pct`
10. `test_blocked_at_100_pct`
11. `test_blocked_after_total_increased_unblocks`
12. `test_subscription_charged_on_day_tick`
13. `test_subscription_blocked_when_budget_full` — does it go into debt? No, `BUDGET_BLOCKED` event + subscription suspended
14. `test_decimal_precision_no_float_drift`
15. `test_concurrent_charges_no_race`

## Error classes

```python
class CostError(Exception): ...
class UnknownTool(CostError): ...
class InvalidPricingFormula(CostError): ...
```

## Definition of Done

- [ ] Pricing catalog (LLM + tool) is working
- [ ] Reservation pattern is tested
- [ ] DAY_TICK consumption integrated (Clock mock)
- [ ] Budget warning + blocked thresholds are working
- [ ] Decimal precision is maintained
- [ ] Coverage ≥ 85%
- [ ] Configuration: `pricing.toml` is loaded

## File skeleton

```
modules/cost/
├── PLAN.md
├── __init__.py
├── pricing.py
├── budget.py
├── subscriptions.py
├── models.py            # Money, Category, Reservation
├── exceptions.py
├── pricing.toml         # default pricing catalog (LLM)
├── migrations/001_init.sql
└── tests/
    ├── test_pricing.py
    ├── test_budget.py
    ├── test_reservations.py
    └── test_subscriptions.py
```

## Open questions

- If we reject a subscription due to budget constraints, no debt should accumulate — if the budget is later increased, should "missed payments" be collected in bulk? Default recommendation: **no, a missed payment is missed**; the subscription remains suspended. The Board re-activates it manually.
