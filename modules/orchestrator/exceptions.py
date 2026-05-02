"""Errors raised by the Orchestrator (Module 11)."""
from __future__ import annotations


class OrchestratorError(Exception):
    """Base class for orchestrator faults."""

    code: str = "orchestrator_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class RedisUnavailable(OrchestratorError):
    """The backing message queue is unreachable (circuit breaker tripped)."""

    code = "queue_unavailable"


class InvalidTarget(OrchestratorError):
    """A send referenced an agent that does not exist or has been fired."""

    code = "invalid_target"


class MessageTooLarge(OrchestratorError):
    """Content exceeded the per-message size cap."""

    code = "message_too_large"


__all__ = [
    "InvalidTarget",
    "MessageTooLarge",
    "OrchestratorError",
    "RedisUnavailable",
]
