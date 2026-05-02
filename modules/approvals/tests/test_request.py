"""``request()`` semantics: persistence, idempotency, blocked signal."""
from __future__ import annotations

from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    Approvals,
    ApprovalStatus,
    RouteTarget,
)
from modules.event_store import EventKind, EventStore


def test_request_creates_pending(
    approvals: Approvals, event_store: EventStore
) -> None:
    a = approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr-1",
        payload={"role": "engineer"},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r1",
    )
    assert a.status is ApprovalStatus.PENDING
    assert a.request_id == "r1"
    assert a.kind is ApprovalKind.HIRE
    assert a.requester_id == "hr-1"
    assert a.route.target is RouteTarget.BOARD

    requested = [
        e for e in event_store.read() if e.kind is EventKind.APPROVAL_REQUESTED
    ]
    assert len(requested) == 1
    assert requested[0].payload["approval_id"] == "r1"


def test_idempotent_same_request_id(
    approvals: Approvals, event_store: EventStore
) -> None:
    a = approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr-1",
        payload={"role": "engineer"},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r1",
    )
    b = approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr-1",
        payload={"role": "engineer"},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r1",
    )
    assert a.request_id == b.request_id
    # Only one APPROVAL_REQUESTED event was emitted.
    requested = [
        e for e in event_store.read() if e.kind is EventKind.APPROVAL_REQUESTED
    ]
    assert len(requested) == 1


def test_is_blocked_when_pending(approvals: Approvals) -> None:
    assert approvals.is_blocked("agent-x") is False
    approvals.request(
        kind=ApprovalKind.EXTERNAL_ACTION,
        requester_id="agent-x",
        payload={"endpoint": "/post"},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="rb1",
    )
    assert approvals.is_blocked("agent-x") is True


def test_pending_for_route_correctly_filters(approvals: Approvals) -> None:
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="b1",
    )
    approvals.request(
        kind=ApprovalKind.FIRE_DEPTH_1,
        requester_id="alice",
        payload={},
        route=ApprovalRoute(RouteTarget.AGENT, agent_id="manager-9"),
        request_id="a1",
    )
    board_pending = approvals.pending_for(ApprovalRoute(RouteTarget.BOARD))
    agent_pending = approvals.pending_for(
        ApprovalRoute(RouteTarget.AGENT, agent_id="manager-9")
    )
    assert {a.request_id for a in board_pending} == {"b1"}
    assert {a.request_id for a in agent_pending} == {"a1"}
