"""Cost engine exceptions."""
from __future__ import annotations


class CostError(Exception):
    """Base for all cost-engine errors."""


class UnknownTool(CostError):
    """Estimate requested for a tool that is not registered."""


class InvalidPricingFormula(CostError):
    """A pricing formula produced an invalid value (negative, non-numeric)."""


class OverBudget(CostError):
    """A reservation cannot be granted because the budget is full."""


class UnknownReservation(CostError):
    """commit/release called with an unknown reservation id."""


class ReservationAlreadyClosed(CostError):
    """commit/release called against a non-open reservation."""
