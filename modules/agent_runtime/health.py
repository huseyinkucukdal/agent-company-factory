"""Per-agent health-signal aggregator.

Tracks the data Orchestrator uses to decide an agent is unhealthy. All
methods are O(1) and lock-free — the agent loop is single-threaded.
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime

from .models import HealthSignals

_REPEAT_WINDOW = 5


def _jaccard(a: str, b: str) -> float:
    """Word-set Jaccard similarity — cheap stand-in for cosine."""
    if not a or not b:
        return 0.0
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


class HealthTracker:
    def __init__(self, *, now: datetime) -> None:
        self._lock = threading.Lock()
        self._last_heartbeat = now
        self._consecutive_errors = 0
        self._parse_failures = 0
        self._total_turns = 0
        self._sum_turn_seconds = 0.0
        self._recent_responses: deque[str] = deque(maxlen=_REPEAT_WINDOW)
        self._repeat_count = 0

    # ----------------------------------------------------------- recorders

    def record_heartbeat(self, ts: datetime) -> None:
        with self._lock:
            self._last_heartbeat = ts

    def record_turn(self, *, duration_seconds: float, ts: datetime) -> None:
        with self._lock:
            self._total_turns += 1
            self._sum_turn_seconds += max(0.0, duration_seconds)
            self._last_heartbeat = ts

    def record_error(self) -> None:
        with self._lock:
            self._consecutive_errors += 1

    def reset_error_streak(self) -> None:
        with self._lock:
            self._consecutive_errors = 0

    def record_parse_failure(self) -> None:
        with self._lock:
            self._parse_failures += 1

    def record_response(self, text: str) -> None:
        with self._lock:
            for prev in self._recent_responses:
                if _jaccard(prev, text) > 0.9:
                    self._repeat_count += 1
                    break
            self._recent_responses.append(text)

    # ------------------------------------------------------------ snapshot

    def snapshot(self) -> HealthSignals:
        with self._lock:
            avg = (
                self._sum_turn_seconds / self._total_turns
                if self._total_turns
                else 0.0
            )
            return HealthSignals(
                last_heartbeat=self._last_heartbeat,
                consecutive_errors=self._consecutive_errors,
                parse_failures=self._parse_failures,
                repeat_response_count=self._repeat_count,
                avg_turn_seconds=avg,
                total_turns=self._total_turns,
            )


__all__ = ["HealthTracker"]
