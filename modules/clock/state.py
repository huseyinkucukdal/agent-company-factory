"""Clock state value objects."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

_REAL_SECONDS_PER_DAY = 86400.0
_DEFAULT_REAL_SECONDS_PER_COMPANY_DAY = 3600.0  # 1 wall-hour = 1 company-day


class ClockState(StrEnum):
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"


@dataclass(frozen=True)
class ClockRate:
    """How many real-world seconds make up one company-day."""

    real_seconds_per_company_day: float = _DEFAULT_REAL_SECONDS_PER_COMPANY_DAY

    def __post_init__(self) -> None:
        if self.real_seconds_per_company_day <= 0:
            raise ValueError("real_seconds_per_company_day must be positive")

    @property
    def factor(self) -> float:
        """Multiply real elapsed seconds by this to get company elapsed seconds."""
        return _REAL_SECONDS_PER_DAY / self.real_seconds_per_company_day

    @staticmethod
    def realtime() -> ClockRate:
        return ClockRate(_DEFAULT_REAL_SECONDS_PER_COMPANY_DAY)

    @staticmethod
    def fast(speedup: float) -> ClockRate:
        if speedup <= 0:
            raise ValueError("speedup must be positive")
        return ClockRate(_DEFAULT_REAL_SECONDS_PER_COMPANY_DAY / speedup)
