"""Public dataclasses used by the Security Agent.

All payloads are pydantic-frozen dataclasses for cheap construction and
immutability — they cross module boundaries (Approvals, Event Store) and
should never mutate after creation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class SecurityFinding:
    """One signal raised by a static rule or proactive scan."""

    severity: Severity
    rule: str
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""
    subject_agent: str | None = None


class ReviewOutcome(StrEnum):
    """Outcome of :meth:`SecurityPolicy.review_approval`."""

    APPROVED = "approved"     # static rules cleared the request
    DENIED = "denied"         # at least one CRITICAL finding
    DEFERRED = "deferred"     # routed to security agent for LLM judgement


@dataclass(frozen=True)
class ReviewResult:
    """The verdict returned to the caller (typically Approvals)."""

    outcome: ReviewOutcome
    findings: list[SecurityFinding]
    reason: str = ""


__all__ = [
    "ReviewOutcome",
    "ReviewResult",
    "SecurityFinding",
    "Severity",
]
