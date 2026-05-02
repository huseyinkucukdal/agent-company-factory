"""Deeper fire (depth >= 2) and unauthorised cases."""
from __future__ import annotations

from modules.event_store import EventKind, EventStore
from modules.identity import FireResult, Org, Role, Status

from .conftest import bootstrap_ceo, bootstrap_hr, hire


def test_fire_depth_2_immediate_with_notify(
    org: Org, event_store: EventStore
) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    eng = hire(org, Role.MEMBER, cto)

    out = org.fire(actor_id=ceo, target_id=eng, reason="redundancy", via_hr=True)
    assert out.result is FireResult.DIRECT_FIRED
    assert org.get(eng).status is Status.FIRED

    notifies = event_store.read(kinds=[EventKind.AGENT_FIRE_NOTIFY])
    assert len(notifies) == 1
    assert notifies[0].payload["to_agent"] == cto
    assert notifies[0].payload["fired_agent"] == eng


def test_fire_depth_3_immediate_with_notify(
    org: Org, event_store: EventStore
) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    lead = hire(org, Role.MEMBER, cto)
    junior = hire(org, Role.MEMBER, lead)

    out = org.fire(actor_id=ceo, target_id=junior, reason="performance below bar", via_hr=True)
    assert out.result is FireResult.DIRECT_FIRED
    notifies = event_store.read(kinds=[EventKind.AGENT_FIRE_NOTIFY])
    assert notifies[0].payload["to_agent"] == lead


def test_fire_self_denied(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    out = org.fire(actor_id=cto, target_id=cto, reason="performance below bar", via_hr=True)
    assert out.result is FireResult.DENIED
    assert out.reason == "self_fire"


def test_fire_unrelated_agent_denied(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    cfo = hire(org, Role.MEMBER, ceo)
    # Siblings: cto and cfo. cto can't fire cfo.
    out = org.fire(actor_id=cto, target_id=cfo, reason="performance below bar", via_hr=True)
    assert out.result is FireResult.DENIED
    assert out.reason == "not_subordinate"


def test_fire_without_via_hr_denied(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    eng = hire(org, Role.MEMBER, cto)
    out = org.fire(actor_id=cto, target_id=eng, reason="performance below bar", via_hr=False)
    assert out.result is FireResult.DENIED
    assert out.reason == "must_route_via_hr"
