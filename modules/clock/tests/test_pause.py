"""Pause/resume state machine tests."""
from __future__ import annotations

from datetime import timedelta

import pytest

from modules.clock import Clock, ClockState

from .conftest import FakeIdleProbe, FakeWallClock


@pytest.mark.asyncio
async def test_pause_blocks_new_messages(
    clock: Clock, idle_probe: FakeIdleProbe
) -> None:
    idle_probe.idle = False  # in-flight work
    await clock.pause(requested_by="board")
    assert clock.is_accepting_messages() is False
    assert clock.state() is ClockState.PAUSING


@pytest.mark.asyncio
async def test_pausing_to_paused_when_idle(
    clock: Clock, idle_probe: FakeIdleProbe
) -> None:
    idle_probe.sequence = [False, True]
    await clock.pause()
    assert clock.state() is ClockState.PAUSING  # first probe says busy
    await clock.pump_once()
    assert clock.state() is ClockState.PAUSED


@pytest.mark.asyncio
async def test_pause_lets_inflight_finish(
    clock: Clock, idle_probe: FakeIdleProbe, wall: FakeWallClock
) -> None:
    idle_probe.sequence = [False, False, True]
    await clock.pause()
    wall.advance(seconds=10)
    await clock.pump_once()
    assert clock.state() is ClockState.PAUSING
    wall.advance(seconds=10)
    await clock.pump_once()
    assert clock.state() is ClockState.PAUSED


@pytest.mark.asyncio
async def test_resume_continues_company_time(
    clock: Clock, idle_probe: FakeIdleProbe, wall: FakeWallClock
) -> None:
    idle_probe.idle = True
    wall.advance(seconds=1800)
    before = clock.now_company()
    await clock.pause()  # idle → goes pausing then paused via maybe_finalise
    wall.advance(seconds=3600)  # 1 hour wall-clock while paused
    await clock.resume()
    wall.advance(seconds=1800)
    after = clock.now_company()
    # Net real running time = 1800 + 1800 = 3600s, the 3600s pause is fully
    # discounted, so company time advanced 12 hours past `before`.
    assert after - before == timedelta(hours=12)


@pytest.mark.asyncio
async def test_paused_time_does_not_advance(
    clock: Clock, wall: FakeWallClock
) -> None:
    wall.advance(seconds=1800)
    snap = clock.now_company()
    await clock.pause()
    await clock.pump_once()  # transition to paused
    wall.advance(seconds=10000)
    assert clock.now_company() == snap


@pytest.mark.asyncio
async def test_double_pause_idempotent(clock: Clock) -> None:
    await clock.pause()
    state_after_first = clock.state()
    await clock.pause()
    assert clock.state() == state_after_first


@pytest.mark.asyncio
async def test_resume_when_running_is_noop(clock: Clock) -> None:
    assert clock.state() is ClockState.RUNNING
    await clock.resume()
    assert clock.state() is ClockState.RUNNING


@pytest.mark.asyncio
async def test_idle_probe_exception_keeps_state_pausing(
    clock: Clock,
) -> None:
    class Boom:
        def is_company_idle(self) -> bool:
            raise RuntimeError("boom")

    clock._idle_probe = Boom()
    await clock.pause()
    await clock.pump_once()
    assert clock.state() is ClockState.PAUSING
