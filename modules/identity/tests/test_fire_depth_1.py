"""Depth-1 fire scenarios — direct manager action, no approval."""
from __future__ import annotations

from modules.event_store import EventKind, EventStore
from modules.identity import FireResult, Org, Role, Status

from .conftest import (
    FakeApprovalRequester,
    bootstrap_ceo,
    bootstrap_hr,
    hire,
)


def test_fire_depth_1_fires_directly_without_approval(
    org: Org,
    event_store: EventStore,
    approvals: FakeApprovalRequester,
) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    eng = hire(org, Role.MEMBER, cto)

    out = org.fire(actor_id=cto, target_id=eng, reason="performance below bar", via_hr=True)
    assert out.result is FireResult.DIRECT_FIRED
    assert out.request_id is None
    assert approvals.requests == []
    assert org.get(eng).status is Status.FIRED
    fired = event_store.read(kinds=[EventKind.AGENT_FIRED])
    assert len(fired) == 1
    assert fired[0].payload["agent_id"] == eng


def test_ceo_fires_direct_report_without_board_approval(
    org: Org, approvals: FakeApprovalRequester
) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)

    out = org.fire(actor_id=ceo, target_id=cto, reason="restructure", via_hr=True)
    assert out.result is FireResult.DIRECT_FIRED
    assert out.request_id is None
    assert approvals.requests == []
    assert org.get(cto).status is Status.FIRED


def test_second_fire_call_sees_target_inactive(
    org: Org, approvals: FakeApprovalRequester
) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    eng = hire(org, Role.MEMBER, cto)

    a = org.fire(actor_id=cto, target_id=eng, reason="performance below bar", via_hr=True)
    b = org.fire(actor_id=cto, target_id=eng, reason="duplicate fire request", via_hr=True)
    assert a.result is FireResult.DIRECT_FIRED
    assert b.result is FireResult.DENIED
    assert b.reason == "target_not_active"
    assert approvals.requests == []
