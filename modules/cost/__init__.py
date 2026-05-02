"""Module 05 — Cost & Budget engine.

Public surface: pricing catalogue, budget engine with reservations, and a
recurring-subscriptions ledger driven by Clock's ``DAY_TICK``.
"""
from __future__ import annotations

from .budget import Budget, migrate
from .exceptions import (
    CostError,
    InvalidPricingFormula,
    OverBudget,
    ReservationAlreadyClosed,
    UnknownReservation,
    UnknownTool,
)
from .models import (
    BudgetState,
    Category,
    ChargeResult,
    Money,
    Reservation,
    ReservationStatus,
    Subscription,
    TokenUsage,
)
from .pricing import CostEstimator, LlmRate, Pricing, load_default_llm_rates
from .subscriptions import Subscriptions

__all__ = [
    "Budget",
    "BudgetState",
    "Category",
    "ChargeResult",
    "CostError",
    "CostEstimator",
    "InvalidPricingFormula",
    "LlmRate",
    "Money",
    "OverBudget",
    "Pricing",
    "Reservation",
    "ReservationAlreadyClosed",
    "ReservationStatus",
    "Subscription",
    "Subscriptions",
    "TokenUsage",
    "UnknownReservation",
    "UnknownTool",
    "load_default_llm_rates",
    "migrate",
]
