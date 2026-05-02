"""Persistence + crash recovery tests."""
from __future__ import annotations

import pytest

from modules.clock import Clock, ClockRate, ClockState
from modules.event_store import EventStore
from modules.storage import CompanyDB

from .conftest import FakeIdleProbe, FakeWallClock


@pytest.mark.asyncio
async def test_persistence_roundtrip(
    company_db: CompanyDB,
    event_store: EventStore,
    wall: FakeWallClock,
    idle_probe: FakeIdleProbe,
) -> None:
    rate = ClockRate.fast(3600)
    c1 = Clock(company_db, event_store, idle_probe, rate=rate, wall_now=wall)
    c1.migrate()
    wall.advance(seconds=1)
    await c1.pump_once()  # day 1 ticked

    # Simulate process restart: brand new Clock, same DB.
    c2 = Clock(company_db, event_store, idle_probe, rate=rate, wall_now=wall)
    c2.migrate()
    assert c2.state() is ClockState.RUNNING
    assert c2._last_day_ticked == 1
    # Company time must continue smoothly, not reset.
    expected = c1.now_company()
    assert c2.now_company() == expected


@pytest.mark.asyncio
async def test_crash_during_paused_extends_paused_time(
    company_db: CompanyDB,
    event_store: EventStore,
    wall: FakeWallClock,
    idle_probe: FakeIdleProbe,
) -> None:
    idle_probe.idle = True
    c1 = Clock(company_db, event_store, idle_probe, wall_now=wall)
    c1.migrate()
    wall.advance(seconds=1800)
    snap = c1.now_company()
    await c1.pause()
    await c1.pump_once()
    assert c1.state() is ClockState.PAUSED

    # Process down: 1 hour wall passes, then we restart.
    wall.advance(seconds=3600)
    c2 = Clock(company_db, event_store, idle_probe, wall_now=wall)
    c2.migrate()
    assert c2.state() is ClockState.PAUSED
    assert c2.now_company() == snap

    # Resume and ensure no jump in company time.
    await c2.resume()
    assert c2.now_company() == snap


@pytest.mark.asyncio
async def test_pausing_state_persisted_across_restart(
    company_db: CompanyDB,
    event_store: EventStore,
    wall: FakeWallClock,
    idle_probe: FakeIdleProbe,
) -> None:
    idle_probe.idle = False
    c1 = Clock(company_db, event_store, idle_probe, wall_now=wall)
    c1.migrate()
    await c1.pause()
    assert c1.state() is ClockState.PAUSING

    c2 = Clock(company_db, event_store, idle_probe, wall_now=wall)
    c2.migrate()
    assert c2.state() is ClockState.PAUSING
    # After restart, idle probe says we are idle now.
    idle_probe.idle = True
    await c2.pump_once()
    assert c2.state() is ClockState.PAUSED


@pytest.mark.asyncio
async def test_start_and_stop_runs_pump(
    clock: Clock,
    event_store: EventStore,
    wall: FakeWallClock,
) -> None:
    import asyncio

    # Use a tiny interval; real-time wall, but day_tick rate is realtime so
    # we won't actually emit any tick — we just verify the loop starts and
    # stops cleanly without leaking the task.
    await clock.start(interval=0.01)
    await asyncio.sleep(0.05)
    await clock.stop()
    assert clock._task is None
