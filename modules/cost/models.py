"""Domain models for the Cost & Budget engine.

All amounts use :class:`decimal.Decimal` quantised to four decimal places
(``0.0001``). Persistence stores integer "units" of ``1e-4`` USD to avoid
binary-float drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

QUANT = Decimal("0.0001")
UNIT_SCALE = 10_000  # 1 unit = 0.0001 USD


def _to_decimal(value: Decimal | str | int | float) -> Decimal:
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Money:
    """Non-negative USD amount with 4-decimal precision."""

    amount_usd: Decimal

    def __post_init__(self) -> None:
        normalised = _to_decimal(self.amount_usd)
        if normalised < 0:
            raise ValueError(f"Money cannot be negative: {normalised}")
        object.__setattr__(self, "amount_usd", normalised)

    @classmethod
    def of(cls, value: Decimal | str | int | float) -> Money:
        return cls(_to_decimal(value))

    @classmethod
    def zero(cls) -> Money:
        return cls(Decimal("0.0000"))

    @classmethod
    def from_units(cls, units: int) -> Money:
        return cls(Decimal(units) / Decimal(UNIT_SCALE))

    def to_units(self) -> int:
        return int((self.amount_usd * UNIT_SCALE).to_integral_value(
            rounding=ROUND_HALF_UP
        ))

    def __add__(self, other: Money) -> Money:
        return Money(self.amount_usd + other.amount_usd)

    def __sub__(self, other: Money) -> Money:
        return Money(self.amount_usd - other.amount_usd)

    def __ge__(self, other: Money) -> bool:
        return self.amount_usd >= other.amount_usd

    def __gt__(self, other: Money) -> bool:
        return self.amount_usd > other.amount_usd

    def __le__(self, other: Money) -> bool:
        return self.amount_usd <= other.amount_usd

    def __lt__(self, other: Money) -> bool:
        return self.amount_usd < other.amount_usd


class Category(StrEnum):
    LLM = "llm"
    TOOL = "tool"
    HOSTING = "hosting"
    DOMAIN = "domain"
    EMAIL = "email"
    ADS = "ads"
    PAYMENT = "payment"
    OTHER = "other"


class ReservationStatus(StrEnum):
    OPEN = "open"
    COMMITTED = "committed"
    RELEASED = "released"


@dataclass(frozen=True)
class Reservation:
    id: str
    amount: Money
    ref: str
    status: ReservationStatus
    created_at: datetime


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class BudgetState:
    total: Money
    spent: Money
    reserved: Money
    remaining: Money
    by_category: dict[Category, Money] = field(default_factory=dict)
    blocked: bool = False
    warning_threshold_pct: float = 80.0


@dataclass(frozen=True)
class ChargeResult:
    ok: bool
    reason: str | None = None
    expense_id: str | None = None

    @property
    def over_budget(self) -> bool:
        return not self.ok and self.reason == "over_budget"


@dataclass(frozen=True)
class Subscription:
    id: str
    description: str
    amount_per_company_month: Money
    category: Category
    next_charge_company_day: int
    period_days: int = 30
    cancelled: bool = False


# threshold constants — central for tests
WARNING_PCT = Decimal("80")
BLOCKED_PCT = Decimal("100")
