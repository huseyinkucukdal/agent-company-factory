"""Domain types for the Agent Runtime."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal


class MessageKind(StrEnum):
    USER_REQUEST = "user_request"
    AGENT_MESSAGE = "agent_message"
    APPROVAL_DECIDED = "approval_decided"
    ORPHAN = "orphan"
    NOTIFY = "notify"


class AgentStatus(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"


@dataclass(frozen=True)
class IncomingMessage:
    content: str
    kind: MessageKind
    from_agent: str | None = None
    correlation_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- LLM events

TurnEventType = Literal["text", "tool_use", "stop"]


@dataclass(frozen=True)
class TurnEvent:
    """A single chunk yielded by :class:`LLMClient.run_turn`.

    Tests script these explicitly; the real Claude Agent SDK adapter
    translates SDK events into the same shape.
    """

    type: TurnEventType
    text: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    tool_input: Mapping[str, Any] = field(default_factory=dict)
    stop_reason: str = ""


@dataclass(frozen=True)
class HealthSignals:
    last_heartbeat: datetime
    consecutive_errors: int = 0
    parse_failures: int = 0
    repeat_response_count: int = 0
    avg_turn_seconds: float = 0.0
    total_turns: int = 0


__all__ = [
    "AgentStatus",
    "HealthSignals",
    "IncomingMessage",
    "MessageKind",
    "TurnEvent",
    "TurnEventType",
]
