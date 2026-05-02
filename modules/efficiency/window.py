"""Bounded in-memory sliding window of recent events.

Holds at most ``max_window_seconds`` worth of events. Pruning runs on
every append; old events drop off the head. The window is single-threaded
— :class:`EfficiencyService` calls it from one task only.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import datetime, timedelta

from modules.event_store import Event


class MetricWindow:
    """Append-only ring of recent :class:`Event` objects, pruned by age."""

    def __init__(self, *, max_age_seconds: int) -> None:
        self._max_age = timedelta(seconds=max_age_seconds)
        self._buf: deque[Event] = deque()

    def append(self, event: Event) -> None:
        self._buf.append(event)
        # Prune by *latest* event's clock so test-time injections are stable.
        cutoff = event.ts_real - self._max_age
        while self._buf and self._buf[0].ts_real < cutoff:
            self._buf.popleft()

    def prune(self, now: datetime) -> None:
        cutoff = now - self._max_age
        while self._buf and self._buf[0].ts_real < cutoff:
            self._buf.popleft()

    def events(self) -> list[Event]:
        return list(self._buf)

    def in_last(self, *, now: datetime, seconds: int) -> list[Event]:
        cutoff = now - timedelta(seconds=seconds)
        # Walk from the end: events are sorted by ts_real (append order).
        out: list[Event] = []
        for e in reversed(self._buf):
            if e.ts_real < cutoff:
                break
            out.append(e)
        out.reverse()
        return out

    def __len__(self) -> int:
        return len(self._buf)

    def extend(self, events: Iterable[Event]) -> None:
        for e in events:
            self.append(e)
