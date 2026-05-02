"""Domain models for the identity module."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    CEO = "ceo"
    HR = "hr"
    SECURITY = "security"
    MEMBER = "member"


SPECIAL_ROLES: frozenset[Role] = frozenset({Role.CEO, Role.HR, Role.SECURITY})


class Status(StrEnum):
    ACTIVE = "active"
    FIRED = "fired"


class FireResult(StrEnum):
    DIRECT_FIRED = "direct_fired"
    PENDING_APPROVAL = "pending_approval"
    DENIED = "denied"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"


# Sentinel decider id for approvals that escalate to the Board.
BOARD_DECIDER = "BOARD"


@dataclass(frozen=True)
class Agent:
    id: str
    role: Role
    persona_ref: str
    reports_to: str | None
    status: Status
    hired_at: datetime
    first_name: str = ""
    last_name: str = ""
    role_title: str | None = None
    role_description: str | None = None
    fired_at: datetime | None = None
    fired_by: str | None = None
    fire_reason: str | None = None


@dataclass(frozen=True)
class FireOutcome:
    """Internal result type returned by :meth:`Org.fire`."""

    result: FireResult
    reason: str | None = None
    request_id: str | None = None
