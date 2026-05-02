"""Health-tracker tests."""
from __future__ import annotations

from datetime import UTC, datetime

from modules.agent_runtime import HealthTracker


def _t() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def test_initial_snapshot() -> None:
    h = HealthTracker(now=_t())
    s = h.snapshot()
    assert s.consecutive_errors == 0
    assert s.total_turns == 0
    assert s.avg_turn_seconds == 0.0


def test_turn_durations_average() -> None:
    h = HealthTracker(now=_t())
    h.record_turn(duration_seconds=1.0, ts=_t())
    h.record_turn(duration_seconds=3.0, ts=_t())
    s = h.snapshot()
    assert s.total_turns == 2
    assert s.avg_turn_seconds == 2.0


def test_repeat_response_detected() -> None:
    h = HealthTracker(now=_t())
    h.record_response("the quick brown fox jumps over the lazy dog")
    h.record_response("the quick brown fox jumps over the lazy dog")
    h.record_response("hello world")
    s = h.snapshot()
    assert s.repeat_response_count >= 1


def test_error_streak_resets() -> None:
    h = HealthTracker(now=_t())
    h.record_error()
    h.record_error()
    assert h.snapshot().consecutive_errors == 2
    h.reset_error_streak()
    assert h.snapshot().consecutive_errors == 0
