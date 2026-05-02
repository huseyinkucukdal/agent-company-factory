"""Tests for proactive scanning + auto_flag side effects."""
from __future__ import annotations

from datetime import timedelta

import pytest

from modules.approvals import ApprovalKind, Approvals, ApprovalStatus
from modules.event_store import EventKind, EventStore
from modules.security_agent import SecurityPolicy, Severity
from modules.security_agent.models import SecurityFinding

from .conftest import FakeOrch


@pytest.mark.asyncio
async def test_scan_recent_events_finds_burst(
    policy: SecurityPolicy, events: EventStore,
) -> None:
    for i in range(12):
        events.append(
            EventKind.EXTERNAL_CALL,
            {
                "service": "stripe", "endpoint": "/charge",
                "request_id": f"r{i}", "status": "ok",
            },
            actor="agent_eng",
        )
    findings = await policy.scan_recent_events(window_minutes=5)
    burst = [f for f in findings if f.rule == "high_volume_external_calls"]
    assert len(burst) == 1
    assert burst[0].subject_agent == "agent_eng"


@pytest.mark.asyncio
async def test_auto_flag_high_creates_security_flag_approval(
    policy: SecurityPolicy, approvals: Approvals, events: EventStore,
    bootstrap: dict[str, str],
) -> None:
    finding = SecurityFinding(
        severity=Severity.HIGH,
        rule="email_mass_send",
        description="email to 200 recipients",
        evidence={"count": 200},
        subject_agent=bootstrap["eng"],
    )
    ap = await policy.auto_flag(finding)
    assert ap is not None
    assert ap.kind is ApprovalKind.SECURITY_FLAG
    assert ap.status is ApprovalStatus.PENDING

    flags = events.read(kinds=[EventKind.SECURITY_FLAG])
    assert len(flags) == 1
    assert flags[0].payload["target"] == bootstrap["eng"]
    assert flags[0].payload["severity"] == "high"


@pytest.mark.asyncio
async def test_auto_flag_critical_suspends_offending_agent(
    policy: SecurityPolicy, orch: FakeOrch, bootstrap: dict[str, str],
) -> None:
    finding = SecurityFinding(
        severity=Severity.CRITICAL,
        rule="persona_path_traversal",
        description="boom",
        subject_agent=bootstrap["eng"],
    )
    ap = await policy.auto_flag(finding)
    assert ap is not None
    assert (bootstrap["eng"], "security:persona_path_traversal") in orch.suspended


@pytest.mark.asyncio
async def test_auto_flag_info_is_dropped(
    policy: SecurityPolicy, events: EventStore,
) -> None:
    finding = SecurityFinding(
        severity=Severity.INFO,
        rule="anything",
        description="just a note",
    )
    ap = await policy.auto_flag(finding)
    assert ap is None
    assert events.read(kinds=[EventKind.SECURITY_FLAG]) == []


_ = timedelta
