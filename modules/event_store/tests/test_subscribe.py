"""Tests for synchronous ``subscribe`` callbacks."""
from __future__ import annotations

from modules.event_store import Event, EventKind, EventStore


def test_subscribe_callback_called_on_append(store: EventStore) -> None:
    seen: list[Event] = []
    store.subscribe(seen.append)
    e = store.append(
        EventKind.MESSAGE_SENT,
        {"from_agent": "a", "to_agent": "b", "content": "x"},
    )
    assert len(seen) == 1
    assert seen[0].id == e.id


def test_subscribe_exception_does_not_break_others(store: EventStore) -> None:
    seen: list[Event] = []

    def bad(_: Event) -> None:
        raise RuntimeError("oops")

    store.subscribe(bad)
    store.subscribe(seen.append)
    store.append(
        EventKind.MESSAGE_SENT,
        {"from_agent": "a", "to_agent": "b", "content": "x"},
    )
    assert len(seen) == 1


def test_unsubscribe_stops_delivery(store: EventStore) -> None:
    seen: list[Event] = []
    handle = store.subscribe(seen.append)
    store.append(
        EventKind.MESSAGE_SENT,
        {"from_agent": "a", "to_agent": "b", "content": "1"},
    )
    store.unsubscribe(handle)
    store.append(
        EventKind.MESSAGE_SENT,
        {"from_agent": "a", "to_agent": "b", "content": "2"},
    )
    assert len(seen) == 1
