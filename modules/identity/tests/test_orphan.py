"""Orphaning and post-fire org-tree integrity."""
from __future__ import annotations

from modules.event_store import EventKind, EventStore
from modules.identity import Org, Role

from .conftest import bootstrap_ceo, bootstrap_hr, hire


def test_orphan_event_emitted_when_manager_fired(
    org: Org, event_store: EventStore
) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    eng_a = hire(org, Role.MEMBER, cto)
    eng_b = hire(org, Role.MEMBER, cto)
    # Insert a third level so firing the lead leaves one orphaned report.
    lead = hire(org, Role.MEMBER, cto)
    sub = hire(org, Role.MEMBER, lead)

    # CEO fires `lead`; lead's reports become orphans.
    out = org.fire(actor_id=ceo, target_id=lead, reason="team reorganisation", via_hr=True)
    assert out.result.value == "direct_fired"

    orphan_events = event_store.read(kinds=[EventKind.AGENT_ORPHANED])
    orphan_ids = {e.payload["agent_id"] for e in orphan_events}
    assert orphan_ids == {sub}

    # `sub` is detached — reports_to is now NULL.
    assert org.manager_of(sub) is None
    # The siblings of `lead` are unaffected.
    assert org.manager_of(eng_a) == cto
    assert org.manager_of(eng_b) == cto


def test_no_orphan_events_when_target_has_no_reports(
    org: Org, event_store: EventStore
) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    eng = hire(org, Role.MEMBER, cto)

    org.fire(actor_id=ceo, target_id=eng, reason="performance below bar", via_hr=True)
    assert event_store.read(kinds=[EventKind.AGENT_ORPHANED]) == []
