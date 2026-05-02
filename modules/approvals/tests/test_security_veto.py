"""Security pre-veto flow."""
from __future__ import annotations

from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    Approvals,
    ApprovalStatus,
    Decision,
    RouteTarget,
)


def test_security_veto_blocks_board_approval(approvals: Approvals) -> None:
    a = approvals.request(
        kind=ApprovalKind.EXPENSE,
        requester_id="cfo",
        payload={"amount_cents": 50_000},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="e1",
    )
    # Default policy: EXPENSE requires security pre-veto.
    assert a.route.require_security is True
    pending_security = approvals.pending_for(
        ApprovalRoute(RouteTarget.SECURITY)
    )
    assert [x.request_id for x in pending_security] == ["e1"]
    # Security DENIES — main approval is already DENIED.
    decided = approvals.decide("e1", "security-agent", Decision.DENY)
    assert decided.status is ApprovalStatus.DENIED
    assert decided.security_decision is Decision.DENY


def test_security_approve_lets_through_to_target(approvals: Approvals) -> None:
    approvals.request(
        kind=ApprovalKind.EXTERNAL_ACTION,
        requester_id="engineer",
        payload={"endpoint": "POST /thing"},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="x1",
    )
    # Security approves.
    after_security = approvals.decide(
        "x1", "security-agent", Decision.APPROVE
    )
    assert after_security.status is ApprovalStatus.PENDING
    assert after_security.security_decision is Decision.APPROVE
    # Now the route's target party can decide.
    pending_board = approvals.pending_for(ApprovalRoute(RouteTarget.BOARD))
    assert {a.request_id for a in pending_board} == {"x1"}
    final = approvals.decide("x1", "user-board", Decision.APPROVE)
    assert final.status is ApprovalStatus.APPROVED


def test_pending_for_security_excludes_kinds_without_veto(
    approvals: Approvals,
) -> None:
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="h1",
    )
    pending_security = approvals.pending_for(
        ApprovalRoute(RouteTarget.SECURITY)
    )
    assert pending_security == []


def test_pending_for_board_hides_until_security_acts(
    approvals: Approvals,
) -> None:
    approvals.request(
        kind=ApprovalKind.EXPENSE,
        requester_id="cfo",
        payload={"amount_cents": 100},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id="e2",
    )
    # Before security acts the board does NOT see this in its queue.
    assert approvals.pending_for(ApprovalRoute(RouteTarget.BOARD)) == []
    approvals.decide("e2", "security-agent", Decision.APPROVE)
    assert {
        a.request_id
        for a in approvals.pending_for(ApprovalRoute(RouteTarget.BOARD))
    } == {"e2"}
