"""Typed payloads for each :class:`EventKind`.

Every kind has a registered :class:`pydantic.BaseModel`. ``EventStore.append``
looks the model up by kind and validates the caller-supplied payload before
serialising it. Unknown extra fields are rejected to catch typos early.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .kinds import EventKind


class _Payload(BaseModel):
    """Base class. Forbid extra fields so typos surface as validation errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------- messaging


class MessageSentPayload(_Payload):
    from_agent: str
    to_agent: str
    content: str
    thread_id: str | None = None


class MessageDeliveredPayload(_Payload):
    message_id: str
    to_agent: str
    thread_id: str | None = None


# ------------------------------------------------------------------------ tools


class ToolCalledPayload(_Payload):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class ToolResultPayload(_Payload):
    tool: str
    request_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class ToolDeniedPayload(_Payload):
    tool: str
    request_id: str
    reason: str


# -------------------------------------------------------------- agent lifecycle


class AgentCreatedPayload(_Payload):
    agent_id: str
    role: str
    manager_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    role_title: str | None = None


class AgentFiredPayload(_Payload):
    agent_id: str
    by_agent_id: str
    reason: str | None = None


class AgentHiredPayload(_Payload):
    agent_id: str
    role: str
    by_agent_id: str
    manager_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    role_title: str | None = None


class AgentFireNotifyPayload(_Payload):
    to_agent: str
    fired_agent: str
    actor: str
    reason: str | None = None


class AgentOrphanedPayload(_Payload):
    agent_id: str
    former_manager_id: str

class AgentHeartbeatPayload(_Payload):
    agent_id: str
    state: str


class AgentHealthAlertPayload(_Payload):
    agent_id: str
    issue: str
    severity: str = "warn"
    detail: str | None = None


class AgentTurnTruncatedPayload(_Payload):
    agent_id: str
    reason: str
    tool_invocations: int = 0


class AgentTurnCompletedPayload(_Payload):
    agent_id: str
    task_done: bool
    duration_seconds: float
    tool_invocations: int = 0


class AgentFeedbackGivenPayload(_Payload):
    feedback_id: str
    target_agent_id: str
    from_agent_id: str
    rating: int


class AgentPromotedPayload(_Payload):
    agent_id: str
    by_agent_id: str
    old_role_title: str | None = None
    new_role_title: str
    old_role_description: str | None = None
    new_role_description: str
    approval_id: str | None = None


# --------------------------------------------------------------------- finance


class ExpenseChargedPayload(_Payload):
    amount_cents: int
    category: str
    note: str | None = None
    request_id: str


class BudgetWarningPayload(_Payload):
    scope: str
    used_cents: int
    limit_cents: int
    ratio: float


class BudgetBlockedPayload(_Payload):
    scope: str
    used_cents: int
    limit_cents: int
    attempted_cents: int


# ------------------------------------------------------------------- approvals


class ApprovalRequestedPayload(_Payload):
    approval_id: str
    requested_by: str
    subject: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecidedPayload(_Payload):
    approval_id: str
    decided_by: str
    decision: str  # "approved" | "rejected"
    note: str | None = None


class ApprovalTimeoutPayload(_Payload):
    approval_id: str


# -------------------------------------------------------------------- security


class SecurityFlagPayload(_Payload):
    severity: str
    target: str
    rule: str
    note: str | None = None


class SecurityVetoPayload(_Payload):
    target: str
    rule: str
    note: str | None = None


# -------------------------------------------------------------------- connector


class ExternalCallPayload(_Payload):
    service: str
    endpoint: str
    request_id: str
    status: str  # "issued" | "ok" | "error"
    duration_ms: int | None = None


# -------------------------------------------------------------- workspace/quota


class QuotaWarningPayload(_Payload):
    agent_id: str
    used: int
    limit: int
    ratio: float


class QuotaExceededPayload(_Payload):
    scope: str  # "agent" | "company"
    agent_id: str | None = None
    limit: int
    would_use: int


class QuotaDriftPayload(_Payload):
    agent_id: str
    drift_bytes: int


# ------------------------------------------------------------------------ clock


class ClockPausedPayload(_Payload):
    by: str | None = None


class ClockResumedPayload(_Payload):
    by: str | None = None


class DayTickPayload(_Payload):
    day: int


# ---------------------------------------------------------------------- company


class CompanyCreatedPayload(_Payload):
    company_id: str
    name: str


class CompanyClosedPayload(_Payload):
    company_id: str
    reason: str | None = None


# ---------------------------------------------------------------- inter-company


class LinkRequestedPayload(_Payload):
    link_id: str
    from_company: str
    to_company: str
    purpose: str


class LinkApprovedPayload(_Payload):
    link_id: str


# --------------------------------------------------------------------- efficiency


class EfficiencyFindingOpenedPayload(_Payload):
    finding_id: str
    detector_code: str
    severity: str
    subject_type: str
    subject_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    recommendation: str | None = None


class EfficiencyFindingClosedPayload(_Payload):
    finding_id: str
    detector_code: str
    reason: str  # "resolved" | "manual"


# --------------------------------------------------------------------- registry


PAYLOAD_MODELS: dict[EventKind, type[_Payload]] = {
    EventKind.MESSAGE_SENT: MessageSentPayload,
    EventKind.MESSAGE_DELIVERED: MessageDeliveredPayload,
    EventKind.TOOL_CALLED: ToolCalledPayload,
    EventKind.TOOL_RESULT: ToolResultPayload,
    EventKind.TOOL_DENIED: ToolDeniedPayload,
    EventKind.AGENT_CREATED: AgentCreatedPayload,
    EventKind.AGENT_FIRED: AgentFiredPayload,
    EventKind.AGENT_HIRED: AgentHiredPayload,
    EventKind.AGENT_FIRE_NOTIFY: AgentFireNotifyPayload,
    EventKind.AGENT_ORPHANED: AgentOrphanedPayload,
    EventKind.AGENT_HEARTBEAT: AgentHeartbeatPayload,
    EventKind.AGENT_HEALTH_ALERT: AgentHealthAlertPayload,
    EventKind.AGENT_TURN_TRUNCATED: AgentTurnTruncatedPayload,
    EventKind.AGENT_TURN_COMPLETED: AgentTurnCompletedPayload,
    EventKind.AGENT_FEEDBACK_GIVEN: AgentFeedbackGivenPayload,
    EventKind.AGENT_PROMOTED: AgentPromotedPayload,
    EventKind.EXPENSE_CHARGED: ExpenseChargedPayload,
    EventKind.BUDGET_WARNING: BudgetWarningPayload,
    EventKind.BUDGET_BLOCKED: BudgetBlockedPayload,
    EventKind.APPROVAL_REQUESTED: ApprovalRequestedPayload,
    EventKind.APPROVAL_DECIDED: ApprovalDecidedPayload,
    EventKind.APPROVAL_TIMEOUT: ApprovalTimeoutPayload,
    EventKind.SECURITY_FLAG: SecurityFlagPayload,
    EventKind.SECURITY_VETO: SecurityVetoPayload,
    EventKind.EXTERNAL_CALL: ExternalCallPayload,
    EventKind.QUOTA_WARNING: QuotaWarningPayload,
    EventKind.QUOTA_EXCEEDED: QuotaExceededPayload,
    EventKind.QUOTA_DRIFT: QuotaDriftPayload,
    EventKind.CLOCK_PAUSED: ClockPausedPayload,
    EventKind.CLOCK_RESUMED: ClockResumedPayload,
    EventKind.DAY_TICK: DayTickPayload,
    EventKind.COMPANY_CREATED: CompanyCreatedPayload,
    EventKind.COMPANY_CLOSED: CompanyClosedPayload,
    EventKind.LINK_REQUESTED: LinkRequestedPayload,
    EventKind.LINK_APPROVED: LinkApprovedPayload,
    EventKind.EFFICIENCY_FINDING_OPENED: EfficiencyFindingOpenedPayload,
    EventKind.EFFICIENCY_FINDING_CLOSED: EfficiencyFindingClosedPayload,
}
"""Mapping from kind to its payload model. Every kind must be present."""


def model_for(kind: EventKind) -> type[_Payload]:
    """Return the registered payload model for ``kind``."""
    return PAYLOAD_MODELS[kind]
