"""Clock module: company-time vs real-time, pause/resume, day-tick events.

See ``PLAN.md`` for the full design.
"""
from .clock import Clock
from .exceptions import ClockError, InvalidStateTransition
from .protocols import OrchestratorIdleProbe
from .state import ClockRate, ClockState

__all__ = [
    "Clock",
    "ClockError",
    "ClockRate",
    "ClockState",
    "InvalidStateTransition",
    "OrchestratorIdleProbe",
]
