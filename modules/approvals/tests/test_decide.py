"""Decision state-machine + cancel + event emission."""
from __future__ import annotations

import pytest

from modules.approvals import (
    ApprovalConflict,
    ApprovalKind,
    ApprovalNotFound,
    ApprovalRoute,
    Approvals,
    ApprovalStatus,
    Decision,
    InvalidTransition,
    RouteTarget,
)
from modules.event_store import EventKind, EventStore


def _request(approvals: Approvals, rid: str = "r1") -> str:
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={"role": "eng"},
        route=ApprovalRoute(RouteTarget.BOARD),
        request_id=rid,
    )
    return rid


def test_decide_approve_marks_approved(
    approvals: Approvals, event_store: EventStore
) -> None:
    rid = _request(approvals)
    out = approvals.decide(rid, "user-board", Decision.APPROVE, note="ok")
    assert out.status is ApprovalStatus.APPROVED
    assert out.decided_by == "user-board"
    assert out.note == "ok"
    decided = [
        e for e in event_store.read() if e.kind is EventKind.APPROVAL_DECIDED
    ]
    assert len(decided) == 1
    assert decided[0].payload["decision"] == "approved"


def test_decide_deny_marks_denied(approvals: Approvals) -> None:
    rid = _request(approvals)
    out = approvals.decide(rid, "user-board", Decision.DENY)
    assert out.status is ApprovalStatus.DENIED


def test_decide_after_decision_idempotent(approvals: Approvals) -> None:
    rid = _request(approvals)
    first = approvals.decide(rid, "user-board", Decision.APPROVE)
    second = approvals.decide(rid, "user-board", Decision.APPROVE)
    assert first.status is second.status is ApprovalStatus.APPROVED


def test_decide_conflicting_decision_rejected(approvals: Approvals) -> None:
    rid = _request(approvals)
    approvals.decide(rid, "user-board", Decision.APPROVE)
    with pytest.raises(ApprovalConflict):
        approvals.decide(rid, "user-board", Decision.DENY)


def test_decide_unknown_request(approvals: Approvals) -> None:
    with pytest.raises(ApprovalNotFound):
        approvals.decide("nope", "u", Decision.APPROVE)


def test_event_emitted_on_each_state_change(
    approvals: Approvals, event_store: EventStore
) -> None:
    rid = _request(approvals)
    approvals.decide(rid, "user-board", Decision.APPROVE)
    kinds = [e.kind for e in event_store.read()]
    assert kinds.count(EventKind.APPROVAL_REQUESTED) == 1
    assert kinds.count(EventKind.APPROVAL_DECIDED) == 1


def test_cancel_pending(approvals: Approvals) -> None:
    rid = _request(approvals)
    out = approvals.cancel(rid, by="hr", reason="superseded")
    assert out.status is ApprovalStatus.CANCELLED
    assert out.note == "superseded"


def test_cancel_already_decided_rejected(approvals: Approvals) -> None:
    rid = _request(approvals)
    approvals.decide(rid, "user-board", Decision.APPROVE)
    with pytest.raises(InvalidTransition):
        approvals.cancel(rid, by="hr", reason="late")
