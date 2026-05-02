"""Real-time timeout processing."""
from __future__ import annotations

from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    Approvals,
    ApprovalStatus,
    RouteTarget,
)
from modules.event_store import EventKind, EventStore

from .conftest import FakeClock


def test_timeout_after_default_seconds(
    approvals: Approvals,
    event_store: EventStore,
    clock: FakeClock,
) -> None:
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r1",
    )
    # Just before 7d nothing happens.
    clock.advance(days=6, hours=23)
    assert approvals.process_timeouts() == 0
    assert approvals.get("r1").status is ApprovalStatus.PENDING

    clock.advance(hours=2)
    n = approvals.process_timeouts()
    assert n == 1
    assert approvals.get("r1").status is ApprovalStatus.TIMEOUT
    timeout_events = [
        e for e in event_store.read() if e.kind is EventKind.APPROVAL_TIMEOUT
    ]
    assert len(timeout_events) == 1


def test_security_flag_auto_denies_on_timeout(
    approvals: Approvals, clock: FakeClock
) -> None:
    approvals.request(
        kind=ApprovalKind.SECURITY_FLAG,
        requester_id="security",
        payload={"target": "agent-x"},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="s1",
    )
    clock.advance(minutes=6)
    assert approvals.process_timeouts() == 1
    a = approvals.get("s1")
    assert a.status is ApprovalStatus.DENIED
    assert a.note == "auto_deny_on_timeout"


def test_custom_timeout_override(
    approvals: Approvals, clock: FakeClock
) -> None:
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r1",
        timeout_seconds=60,
    )
    clock.advance(seconds=30)
    assert approvals.process_timeouts() == 0
    clock.advance(seconds=31)
    assert approvals.process_timeouts() == 1
    assert approvals.get("r1").status is ApprovalStatus.TIMEOUT


def test_process_timeouts_idempotent(
    approvals: Approvals, clock: FakeClock
) -> None:
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r1",
    )
    clock.advance(days=8)
    assert approvals.process_timeouts() == 1
    assert approvals.process_timeouts() == 0
