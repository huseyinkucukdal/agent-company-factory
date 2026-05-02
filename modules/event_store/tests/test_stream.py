"""Tests for ``EventStore.stream`` (async live tailing)."""
from __future__ import annotations

import asyncio

import pytest

from modules.event_store import Event, EventKind, EventStore


def _msg(i: int) -> dict[str, str]:
    return {"from_agent": "a", "to_agent": "b", "content": str(i)}


@pytest.mark.asyncio
async def test_stream_yields_in_order(store: EventStore) -> None:
    # Pre-seed two events that should arrive as backlog.
    store.append(EventKind.MESSAGE_SENT, _msg(1))
    store.append(EventKind.MESSAGE_SENT, _msg(2))

    received: list[Event] = []
    gen = store.stream(since=0)

    async def consumer() -> None:
        async for evt in gen:
            received.append(evt)
            if len(received) >= 4:
                break

    task = asyncio.create_task(consumer())
    # Give the consumer a tick to drain the backlog and register.
    await asyncio.sleep(0.05)

    store.append(EventKind.MESSAGE_SENT, _msg(3))
    store.append(EventKind.MESSAGE_SENT, _msg(4))

    await asyncio.wait_for(task, timeout=2.0)
    await gen.aclose()

    assert [int(e.payload["content"]) for e in received] == [1, 2, 3, 4]
    ids = [e.id for e in received]
    assert ids == sorted(ids)


@pytest.mark.asyncio
async def test_stream_since_skips_backlog(store: EventStore) -> None:
    a = store.append(EventKind.MESSAGE_SENT, _msg(1))
    store.append(EventKind.MESSAGE_SENT, _msg(2))

    received: list[Event] = []
    gen = store.stream(since=a.id)

    async def consumer() -> None:
        async for evt in gen:
            received.append(evt)
            if len(received) >= 2:
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    store.append(EventKind.MESSAGE_SENT, _msg(3))

    await asyncio.wait_for(task, timeout=2.0)
    await gen.aclose()

    assert [int(e.payload["content"]) for e in received] == [2, 3]


@pytest.mark.asyncio
async def test_stream_backpressure_drops_oldest_on_overflow(
    store: EventStore,
) -> None:
    """Tiny buffer; rapid appends; the consumer should still see the latest event."""
    gen = store.stream(since=0, buffer_size=2)

    received: list[Event] = []

    async def consumer() -> None:
        async for evt in gen:
            received.append(evt)
            if int(evt.payload["content"]) == 9:
                break

    task = asyncio.create_task(consumer())
    # Let the stream register its subscription before we start firing.
    await asyncio.sleep(0.05)

    for i in range(10):
        store.append(EventKind.MESSAGE_SENT, _msg(i))

    await asyncio.wait_for(task, timeout=2.0)
    await gen.aclose()

    # We must have seen at least the final event…
    assert any(e.payload["content"] == "9" for e in received)
    # …and we must have dropped at least one (otherwise overflow didn't happen).
    contents = [int(e.payload["content"]) for e in received]
    assert len(contents) < 10


@pytest.mark.asyncio
async def test_stream_unsubscribes_on_close(store: EventStore) -> None:
    gen = store.stream(since=0)

    async def consumer() -> None:
        async for _ in gen:
            break

    # Trigger one event so the generator yields and exits the loop.
    store.append(EventKind.MESSAGE_SENT, _msg(1))
    await asyncio.wait_for(consumer(), timeout=2.0)
    await gen.aclose()

    # Internal stream registry should be empty afterwards.
    assert store._streams == {}
