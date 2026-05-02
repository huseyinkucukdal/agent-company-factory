"""Protocols Clock depends on."""
from typing import Protocol, runtime_checkable


@runtime_checkable
class OrchestratorIdleProbe(Protocol):
    """Reports whether all of the company's agents are currently idle.

    Used by Clock to transition ``pausing → paused`` once in-flight work has
    drained.
    """

    def is_company_idle(self) -> bool: ...
