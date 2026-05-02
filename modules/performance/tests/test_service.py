"""Performance service behaviour."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from modules.approvals import ApprovalRoute, Approvals, RouteTarget
from modules.event_store import EventKind, EventStore
from modules.identity import Org, Role, Status
from modules.performance import Performance
from modules.storage import CompanyDB


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: int) -> None:
        self.t += timedelta(seconds=seconds)


class _NoopFireRequester:
    def request_fire(self, **_kwargs: Any) -> None:
        return None


@pytest.fixture
def company_db(tmp_path: Path) -> Iterator[CompanyDB]:
    db = CompanyDB.init("acme", tmp_path)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def event_store(company_db: CompanyDB, clock: FakeClock) -> EventStore:
    events = EventStore(company_db, company_now=clock.now)
    events.migrate()
    return events


@pytest.fixture
def approvals(
    company_db: CompanyDB,
    event_store: EventStore,
    clock: FakeClock,
) -> Approvals:
    svc = Approvals(company_db, event_store, time_provider=clock.now)
    svc.migrate()
    return svc


@pytest.fixture
def org(company_db: CompanyDB, event_store: EventStore) -> Org:
    svc = Org(company_db, event_store, _NoopFireRequester())
    svc.migrate()
    return svc


@pytest.fixture
def performance(
    company_db: CompanyDB,
    event_store: EventStore,
    org: Org,
    approvals: Approvals,
    clock: FakeClock,
) -> Performance:
    svc = Performance(company_db, event_store, org, approvals, now=clock.now)
    svc.migrate()
    return svc


def _bootstrap_team(org: Org) -> tuple[str, str, str]:
    ceo = org.add_agent(
        role=Role.CEO,
        persona_ref="default",
        reports_to=None,
        requested_by="board",
        via_hr=False,
        bootstrap=True,
    )
    hr = org.add_agent(
        role=Role.HR,
        persona_ref="default",
        reports_to=ceo.id,
        requested_by="board",
        via_hr=False,
        bootstrap=True,
    )
    member = org.add_agent(
        role=Role.MEMBER,
        persona_ref="default",
        reports_to=ceo.id,
        requested_by=hr.id,
        via_hr=True,
        first_name="Taylor",
        last_name="Kim",
        role_title="Engineer",
        role_description="Build product features.",
    )
    return ceo.id, hr.id, member.id


def test_report_combines_event_metrics_and_feedback(
    org: Org,
    event_store: EventStore,
    performance: Performance,
    clock: FakeClock,
) -> None:
    ceo, _hr, member = _bootstrap_team(org)

    event_store.append(
        EventKind.MESSAGE_SENT,
        {"from_agent": ceo, "to_agent": member, "content": "status?"},
        actor=ceo,
        correlation="thread-1",
    )
    clock.advance(12)
    event_store.append(
        EventKind.MESSAGE_SENT,
        {"from_agent": member, "to_agent": ceo, "content": "done"},
        actor=member,
        correlation="thread-1",
    )
    event_store.append(
        EventKind.TOOL_CALLED,
        {"tool": "write_my_workspace", "arguments": {}, "request_id": "t1"},
        actor=member,
    )
    event_store.append(
        EventKind.TOOL_CALLED,
        {"tool": "external_call", "arguments": {}, "request_id": "t2"},
        actor=member,
    )
    event_store.append(
        EventKind.TOOL_RESULT,
        {"tool": "write_my_workspace", "request_id": "t1", "ok": True},
        actor=member,
    )
    event_store.append(
        EventKind.TOOL_RESULT,
        {"tool": "external_call", "request_id": "t2", "ok": False, "error": "x"},
        actor=member,
    )
    event_store.append(
        EventKind.AGENT_TURN_COMPLETED,
        {
            "agent_id": member,
            "task_done": True,
            "duration_seconds": 1.2,
            "tool_invocations": 1,
        },
        actor=member,
    )
    event_store.append(
        EventKind.AGENT_TURN_TRUNCATED,
        {"agent_id": member, "reason": "tool_loop_cap", "tool_invocations": 20},
        actor=member,
    )
    event_store.append(
        EventKind.AGENT_HEALTH_ALERT,
        {"agent_id": member, "issue": "llm_timeout", "severity": "warn"},
        actor=member,
    )

    feedback = performance.give_feedback(
        caller_id=ceo,
        target_id=member,
        rating=5,
        note="Strong delivery.",
    )

    report = performance.report(member)
    assert report.metrics.tasks_completed == 1
    assert report.metrics.messages_sent == 1
    assert report.metrics.messages_received == 1
    assert report.metrics.tool_success_rate == 0.5
    assert report.metrics.avg_response_seconds == 12.0
    assert report.metrics.truncated_turns == 1
    assert report.metrics.health_alerts == 1
    assert report.recent_feedback == [feedback]


def test_feedback_requires_manager_or_ceo(
    org: Org,
    performance: Performance,
) -> None:
    ceo, hr, member = _bootstrap_team(org)
    peer = org.add_agent(
        role=Role.MEMBER,
        persona_ref="default",
        reports_to=ceo,
        requested_by=hr,
        via_hr=True,
        role_title="Designer",
        role_description="Design UI.",
    )

    with pytest.raises(PermissionError, match="not_manager"):
        performance.give_feedback(
            caller_id=peer.id,
            target_id=member,
            rating=3,
            note="Peer feedback is not accepted here.",
        )


def test_role_change_updates_identity_immediately_and_emits_event(
    org: Org,
    approvals: Approvals,
    event_store: EventStore,
    performance: Performance,
) -> None:
    ceo, _hr, member = _bootstrap_team(org)

    outcome = performance.propose_role_change(
        caller_id=ceo,
        target_id=member,
        new_role_title="Senior Engineer",
        new_role_description="Own feature delivery and mentor new members.",
        rationale="Sustained high performance.",
    )

    assert outcome.status == "updated"
    assert outcome.request_id is None

    updated = org.get(member)
    assert updated.role_title == "Senior Engineer"
    assert updated.role_description == "Own feature delivery and mentor new members."
    promoted = event_store.read(kinds=[EventKind.AGENT_PROMOTED])
    assert len(promoted) == 1
    assert promoted[0].payload["agent_id"] == member
    assert approvals.pending_for(ApprovalRoute(target=RouteTarget.BOARD)) == []


def test_fire_updates_identity_immediately_without_board_approval(
    org: Org,
    approvals: Approvals,
    performance: Performance,
) -> None:
    ceo, _hr, member = _bootstrap_team(org)

    outcome = performance.propose_fire(
        caller_id=ceo,
        target_id=member,
        reason="Performance issue.",
    )

    assert outcome.result == "direct_fired"
    assert outcome.request_id is None
    assert org.get(member).status is Status.FIRED
    assert approvals.pending_for(ApprovalRoute(target=RouteTarget.BOARD)) == []
