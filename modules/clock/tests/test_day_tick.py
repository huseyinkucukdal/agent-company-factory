"""DAY_TICK emission tests."""
from __future__ import annotations

import pytest

from modules.clock import Clock, ClockRate
from modules.event_store import EventKind, EventStore

from .conftest import ClockFactory, FakeIdleProbe, FakeWallClock


@pytest.mark.asyncio
async def test_day_tick_emitted_on_boundary(
    clock_factory: ClockFactory, event_store: EventStore, wall: FakeWallClock
) -> None:
    clock = clock_factory(rate=ClockRate.fast(3600))  # 1s = 1 day
    wall.advance(seconds=1)
    await clock.pump_once()
    ticks = event_store.read(kinds=[EventKind.DAY_TICK])
    assert len(ticks) == 1
    assert ticks[0].payload == {"day": 1}


@pytest.mark.asyncio
async def test_day_tick_catches_up_after_long_pause(
    clock_factory: ClockFactory,
    event_store: EventStore,
    wall: FakeWallClock,
    idle_probe: FakeIdleProbe,
) -> None:
    clock = clock_factory(rate=ClockRate.fast(3600))
    idle_probe.idle = True
    wall.advance(seconds=1)
    await clock.pump_once()  # day 1
    await clock.pause()
    await clock.pump_once()
    wall.advance(seconds=100)  # 100 days of wall time, but paused
    await clock.resume()
    wall.advance(seconds=3)  # 3 more company days
    await clock.pump_once()
    ticks = event_store.read(kinds=[EventKind.DAY_TICK])
    days = [t.payload["day"] for t in ticks]
    assert days == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_no_duplicate_ticks_for_same_day(
    clock_factory: ClockFactory, event_store: EventStore, wall: FakeWallClock
) -> None:
    clock = clock_factory(rate=ClockRate.fast(3600))
    wall.advance(seconds=1)
    await clock.pump_once()
    await clock.pump_once()
    await clock.pump_once()
    ticks = event_store.read(kinds=[EventKind.DAY_TICK])
    assert len(ticks) == 1


@pytest.mark.asyncio
async def test_no_tick_before_first_full_day(
    clock: Clock, event_store: EventStore, wall: FakeWallClock
) -> None:
    wall.advance(seconds=1800)
    await clock.pump_once()
    ticks = event_store.read(kinds=[EventKind.DAY_TICK])
    assert ticks == []
