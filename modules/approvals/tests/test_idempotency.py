"""Idempotency, subscribe, and route-validation."""
from __future__ import annotations

import pytest

from modules.approvals import (
    Approval,
    ApprovalKind,
    ApprovalRoute,
    Approvals,
    ApprovalStatus,
    Decision,
    RouteTarget,
)


def test_request_id_idempotent_after_decision(approvals: Approvals) -> None:
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r1",
    )
    approvals.decide("r1", "u", Decision.APPROVE)
    again = approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r1",
    )
    assert again.status is ApprovalStatus.APPROVED


def test_subscribe_receives_state_changes(approvals: Approvals) -> None:
    seen: list[Approval] = []
    handle = approvals.subscribe(seen.append)
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r1",
    )
    approvals.decide("r1", "u", Decision.APPROVE)
    assert [a.status for a in seen] == [
        ApprovalStatus.PENDING,
        ApprovalStatus.APPROVED,
    ]
    approvals.unsubscribe(handle)
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="r2",
    )
    # Still only the previous two events.
    assert len(seen) == 2


def test_route_agent_requires_id() -> None:
    with pytest.raises(ValueError):
        ApprovalRoute(RouteTarget.AGENT)


def test_route_security_cannot_require_security() -> None:
    with pytest.raises(ValueError):
        ApprovalRoute(RouteTarget.SECURITY, require_security=True)
