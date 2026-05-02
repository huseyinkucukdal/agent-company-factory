"""Domain types for the Approval System."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ApprovalKind(StrEnum):
    HIRE = "hire"
    FIRE_DEPTH_1 = "fire_depth_1"
    EXPENSE = "expense"
    EXTERNAL_ACTION = "external_action"
    INTER_COMPANY_LINK = "inter_company_link"
    SECURITY_FLAG = "security_flag"
    BUDGET_INCREASE = "budget_increase"
    ROLE_CHANGE = "role_change"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class Decision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


class RouteTarget(StrEnum):
    """Who can decide the *main* leg of an approval."""

    BOARD = "board"
    AGENT = "agent"  # combined with agent_id
    SECURITY = "security"


@dataclass(frozen=True)
class ApprovalRoute:
    target: RouteTarget
    agent_id: str | None = None
    require_security: bool = False

    def __post_init__(self) -> None:
        if self.target is RouteTarget.AGENT and not self.agent_id:
            raise ValueError("agent_id required when target is AGENT")
        if self.target is not RouteTarget.AGENT and self.agent_id is not None:
            raise ValueError("agent_id only valid when target is AGENT")
        if self.target is RouteTarget.SECURITY and self.require_security:
            raise ValueError(
                "require_security must be False when target is SECURITY"
            )

    def encode_target(self) -> str:
        if self.target is RouteTarget.AGENT:
            return f"agent:{self.agent_id}"
        return self.target.value

    @classmethod
    def decode(cls, encoded: str, *, require_security: bool) -> ApprovalRoute:
        if encoded.startswith("agent:"):
            return cls(
                target=RouteTarget.AGENT,
                agent_id=encoded.split(":", 1)[1],
                require_security=require_security,
            )
        return cls(
            target=RouteTarget(encoded), require_security=require_security
        )


@dataclass(frozen=True)
class Approval:
    request_id: str
    kind: ApprovalKind
    requester_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    route: ApprovalRoute = field(default=ApprovalRoute(RouteTarget.BOARD))
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    decided_at: datetime | None = None
    note: str | None = None
    expires_at: datetime = field(default_factory=lambda: datetime.min)
    created_at: datetime = field(default_factory=lambda: datetime.min)
    security_decision: Decision | None = None
    security_decided_at: datetime | None = None


@dataclass(frozen=True)
class SubscriptionHandle:
    id: str
