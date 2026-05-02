"""Tests for company-time math."""
from __future__ import annotations

from datetime import timedelta

from modules.clock import Clock, ClockRate

from .conftest import EPOCH, ClockFactory, FakeWallClock


def test_now_company_advances_realtime(clock: Clock, wall: FakeWallClock) -> None:
    """Default rate: 3600 real seconds → 1 company day."""
    start = clock.now_company()
    wall.advance(seconds=3600)
    after = clock.now_company()
    assert after - start == timedelta(days=1)


def test_fast_mode_for_simulation(
    clock_factory: ClockFactory, wall: FakeWallClock
) -> None:
    """speedup=3600 → 1 real second = 1 company day."""
    clock = clock_factory(rate=ClockRate.fast(3600))
    start = clock.now_company()
    wall.advance(seconds=1)
    assert clock.now_company() - start == timedelta(days=1)


def test_real_clock_jumps_backward_no_negative_company_time(
    clock: Clock, wall: FakeWallClock
) -> None:
    wall.advance(seconds=600)
    forward = clock.now_company()
    wall.now = EPOCH  # ntp pulled the clock backwards
    after_jump = clock.now_company()
    assert after_jump >= forward


def test_now_company_starts_at_epoch_company(clock: Clock) -> None:
    assert clock.now_company() == EPOCH


def test_invalid_rate_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        ClockRate(0)
    with pytest.raises(ValueError):
        ClockRate.fast(0)
