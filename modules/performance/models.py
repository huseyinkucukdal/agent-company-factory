"""Domain models for performance reporting."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Feedback:
    id: str
    target_agent_id: str
    from_agent_id: str
    rating: int
    note: str
    ts: datetime


@dataclass(frozen=True)
class PerformanceMetrics:
    tasks_completed: int
    messages_sent: int
    messages_received: int
    tool_success_rate: float | None
    avg_response_seconds: float | None
    truncated_turns: int
    health_alerts: int
    tenure_days: int


@dataclass(frozen=True)
class PerformanceReport:
    metrics: PerformanceMetrics
    recent_feedback: list[Feedback]


@dataclass(frozen=True)
class RoleChangeProposal:
    status: str
    request_id: str | None = None


@dataclass(frozen=True)
class FireRequestOutcome:
    result: str
    reason: str | None = None
    request_id: str | None = None
