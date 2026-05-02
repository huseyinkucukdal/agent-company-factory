"""The canonical catalogue of event kinds emitted across the platform."""
from __future__ import annotations

from enum import StrEnum


class EventKind(StrEnum):
    """Every kind of audit-log event the system can emit.

    String-valued so it serialises naturally to JSON and survives DB
    round-trips through the ``events.kind`` TEXT column.
    """

    # messaging
    MESSAGE_SENT = "message.sent"
    MESSAGE_DELIVERED = "message.delivered"

    # tools
    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"
    TOOL_DENIED = "tool.denied"

    # agent lifecycle
    AGENT_CREATED = "agent.created"
    AGENT_FIRED = "agent.fired"
    AGENT_HIRED = "agent.hired"
    AGENT_FIRE_NOTIFY = "agent.fire_notify"
    AGENT_ORPHANED = "agent.orphaned"
    AGENT_HEARTBEAT = "agent.heartbeat"
    AGENT_HEALTH_ALERT = "agent.health_alert"
    AGENT_TURN_TRUNCATED = "agent.turn_truncated"
    AGENT_TURN_COMPLETED = "agent.turn_completed"
    AGENT_FEEDBACK_GIVEN = "agent.feedback_given"
    AGENT_PROMOTED = "agent.promoted"
    AGENT_REASSIGNED = "agent.reassigned"

    # finance
    EXPENSE_CHARGED = "expense.charged"
    BUDGET_WARNING = "budget.warning"
    BUDGET_BLOCKED = "budget.blocked"

    # approvals
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    APPROVAL_TIMEOUT = "approval.timeout"

    # security
    SECURITY_FLAG = "security.flag"
    SECURITY_VETO = "security.veto"

    # connector
    EXTERNAL_CALL = "connector.external_call"

    # workspace / storage
    QUOTA_WARNING = "quota.warning"
    QUOTA_EXCEEDED = "quota.exceeded"
    QUOTA_DRIFT = "quota.drift"

    # clock
    CLOCK_PAUSED = "clock.paused"
    CLOCK_RESUMED = "clock.resumed"
    DAY_TICK = "clock.day_tick"

    # company
    COMPANY_CREATED = "company.created"
    COMPANY_CLOSED = "company.closed"

    # inter-company
    LINK_REQUESTED = "link.requested"
    LINK_APPROVED = "link.approved"

    # efficiency
    EFFICIENCY_FINDING_OPENED = "efficiency.finding.opened"
    EFFICIENCY_FINDING_CLOSED = "efficiency.finding.closed"
