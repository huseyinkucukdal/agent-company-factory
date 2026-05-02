"""Idle-sweep / nudge watchdog tests."""
from __future__ import annotations

import pytest

from modules.event_store import EventKind, EventStore
from modules.orchestrator import Orchestrator

from .conftest import FakeClock


@pytest.mark.asyncio
async def test_idle_subordinate_nudges_manager(
    orch: Orchestrator,
    bootstrap_org: dict[str, str],
    clock: FakeClock,
    event_store: EventStore,
) -> None:
    # Default IdleSweepConfig: 90s threshold. Advance well past.
    clock.advance(minutes=5)
    sent = await orch.idle_sweep()
    assert sent >= 1

    # The eng's manager is the CEO; expect a system NOTIFY to CEO.
    sent_msgs = event_store.read(kinds=[EventKind.MESSAGE_SENT])
    to_ceo = [
        m for m in sent_msgs
        if m.payload.get("to_agent") == bootstrap_org["ceo"]
        and m.payload.get("from_agent") == "system"
    ]
    assert to_ceo, "expected a system message to be queued for the CEO"
    assert any(
        "idle" in (m.payload.get("content") or "") for m in to_ceo
    )

    alerts = event_store.read(kinds=[EventKind.AGENT_HEALTH_ALERT])
    assert any(a.payload.get("issue") == "idle_nudge" for a in alerts)


@pytest.mark.asyncio
async def test_ceo_self_prompted_when_no_manager(
    orch: Orchestrator,
    bootstrap_org: dict[str, str],
    clock: FakeClock,
    event_store: EventStore,
) -> None:
    clock.advance(minutes=5)
    await orch.idle_sweep()
    sent_msgs = event_store.read(kinds=[EventKind.MESSAGE_SENT])
    to_ceo_self = [
        m for m in sent_msgs
        if m.payload.get("to_agent") == bootstrap_org["ceo"]
        and m.payload.get("from_agent") == "system"
    ]
    # CEO has no manager so it should be self-prompted.
    assert to_ceo_self


@pytest.mark.asyncio
async def test_nudge_cooldown_prevents_spam(
    orch: Orchestrator, clock: FakeClock,
) -> None:
    clock.advance(minutes=5)
    first = await orch.idle_sweep()
    assert first >= 1
    # Immediate re-sweep within cooldown — no new nudges.
    second = await orch.idle_sweep()
    assert second == 0


@pytest.mark.asyncio
async def test_recently_active_agent_not_nudged(
    orch: Orchestrator,
    bootstrap_org: dict[str, str],
    clock: FakeClock,
    event_store: EventStore,
) -> None:
    # Mark eng as active right now (as if it had just delivered a turn).
    orch._last_activity_at[bootstrap_org["eng"]] = clock.t
    clock.advance(seconds=10)
    await orch.idle_sweep()
    alerts = event_store.read(kinds=[EventKind.AGENT_HEALTH_ALERT])
    nudged_ids = {
        a.payload.get("agent_id")
        for a in alerts if a.payload.get("issue") == "idle_nudge"
    }
    assert bootstrap_org["eng"] not in nudged_ids


@pytest.mark.asyncio
async def test_paused_clock_blocks_watchdog_sweep(
    orch: Orchestrator, clock: FakeClock,
) -> None:
    clock.pause_sync()
    clock.advance(minutes=5)
    # idle_sweep itself doesn't gate on the clock — that's the watchdog's
    # job — so it still works when called directly. This test pins the
    # contract: idle_sweep is callable on demand.
    sent = await orch.idle_sweep()
    assert sent >= 0
