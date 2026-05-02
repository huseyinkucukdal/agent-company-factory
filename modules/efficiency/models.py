"""Domain types for the Efficiency module."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class FindingStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"


class SubjectType(StrEnum):
    AGENT = "agent"
    MANAGER = "manager"
    COMPANY = "company"
    TOOL = "tool"
    APPROVAL = "approval"


@dataclass(frozen=True)
class FindingCandidate:
    """A detector's output for a single firing subject.

    Detectors return one of these per *currently-firing* subject. The
    :class:`EfficiencyService` then diffs against currently-open findings
    of the same ``detector_code`` to decide which records to open, update,
    or auto-close.
    """

    detector_code: str
    severity: Severity
    subject_type: SubjectType
    subject_id: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str | None = None


@dataclass(frozen=True)
class Finding:
    """A persisted finding record."""

    id: str
    detector_code: str
    severity: Severity
    subject_type: SubjectType
    subject_id: str | None
    opened_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
    occurrences: int
    evidence: dict[str, Any]
    recommendation: str | None
    status: FindingStatus
