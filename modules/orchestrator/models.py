"""Public data shapes used by the Orchestrator."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

# Re-export to keep the module's public surface self-contained.
from modules.agent_runtime import IncomingMessage, MessageKind


class SendResult(StrEnum):
    """Outcome of :meth:`Orchestrator.send`."""

    QUEUED = "queued"
    REJECTED_PAUSED = "rejected_paused"
    REJECTED_RATE_LIMIT = "rejected_rate_limit"
    REJECTED_LOOP = "rejected_loop"
    REJECTED_INVALID_TARGET = "rejected_invalid_target"
    REJECTED_DUPLICATE = "rejected_duplicate"
    REJECTED_TOO_LARGE = "rejected_too_large"


@dataclass(frozen=True)
class MessageEnvelope:
    """One queued unit of work targeted at a single agent's inbox."""

    msg_id: str
    from_agent: str | None
    to_agent: str
    content: str
    kind: MessageKind
    correlation_id: str | None
    enqueued_at: datetime
    attempts: int = 0

    @staticmethod
    def new(
        *, from_agent: str | None, to_agent: str, content: str,
        kind: MessageKind, correlation_id: str | None,
        enqueued_at: datetime,
    ) -> MessageEnvelope:
        return MessageEnvelope(
            msg_id=f"m-{uuid4().hex[:12]}",
            from_agent=from_agent,
            to_agent=to_agent,
            content=content,
            kind=kind,
            correlation_id=correlation_id,
            enqueued_at=enqueued_at,
        )


@dataclass(frozen=True)
class AgentHealth:
    """Aggregated view exposed via :meth:`Orchestrator.health`."""

    agent_id: str
    score: float
    status: str
    consecutive_errors: int
    parse_failures: int
    repeat_response_count: int
    avg_turn_seconds: float
    last_heartbeat: datetime
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DeadLetter:
    """One unprocessable envelope routed to the DLQ."""

    envelope: MessageEnvelope
    reason: str
    moved_at: datetime


@dataclass(frozen=True)
class IdleSweepConfig:
    """Configuration for the idle/nudge watchdog.

    The watchdog runs inside :meth:`Orchestrator.start` and periodically
    looks for agents that have been silent for too long. When one is
    found it gently *nudges* the agent's manager (or, for the CEO, the
    agent itself) with a system message asking them to take stock and
    decide what to do next. This unsticks companies that fall idle after
    completing an initial task without delegating further work.
    """

    enabled: bool = True
    interval_seconds: float = 30.0
    """How often the watchdog wakes up."""

    agent_idle_after_seconds: float = 90.0
    """An agent is considered idle after this much wall-clock silence."""

    nudge_cooldown_seconds: float = 300.0
    """Minimum gap between two nudges aimed at the same agent."""


__all__ = [
    "AgentHealth",
    "DeadLetter",
    "IdleSweepConfig",
    "IncomingMessage",
    "MessageEnvelope",
    "MessageKind",
    "SendResult",
]
