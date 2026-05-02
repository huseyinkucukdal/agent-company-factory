"""Tests for ``EventStore.append``."""
from __future__ import annotations

import threading

import pytest

from modules.event_store import (
    EventKind,
    EventStore,
    PayloadTooLarge,
    PayloadValidationError,
    UnknownEventKind,
)


def _msg(seq: int = 0) -> dict[str, str]:
    return {"from_agent": "a", "to_agent": "b", "content": f"hi {seq}"}


def test_append_returns_monotonic_id(store: EventStore) -> None:
    e1 = store.append(EventKind.MESSAGE_SENT, _msg(1))
    e2 = store.append(EventKind.MESSAGE_SENT, _msg(2))
    e3 = store.append(EventKind.MESSAGE_SENT, _msg(3))
    assert e1.id < e2.id < e3.id
    assert e1.company_id == "acme"


def test_append_persists_payload_roundtrip(store: EventStore) -> None:
    payload = _msg(7)
    appended = store.append(
        EventKind.MESSAGE_SENT, payload, actor="a", correlation="task-1"
    )
    rows = store.read()
    assert len(rows) == 1
    got = rows[0]
    assert got.id == appended.id
    assert got.kind is EventKind.MESSAGE_SENT
    # Stored payload is the validated form (defaults filled in).
    assert got.payload == {**payload, "thread_id": None}
    assert got.payload == appended.payload
    assert got.actor_agent_id == "a"
    assert got.correlation_id == "task-1"


def test_append_unknown_kind_rejected(store: EventStore) -> None:
    with pytest.raises(UnknownEventKind):
        store.append("bogus.kind", {})


def test_append_payload_validation_error(store: EventStore) -> None:
    with pytest.raises(PayloadValidationError):
        store.append(EventKind.MESSAGE_SENT, {"only_field": "x"})


def test_append_payload_extras_forbidden(store: EventStore) -> None:
    with pytest.raises(PayloadValidationError):
        store.append(
            EventKind.MESSAGE_SENT,
            {**_msg(), "unknown_field": True},
        )


def test_payload_too_large_rejected(store: EventStore) -> None:
    huge = "x" * (300 * 1024)
    with pytest.raises(PayloadTooLarge):
        store.append(
            EventKind.MESSAGE_SENT,
            {"from_agent": "a", "to_agent": "b", "content": huge},
        )


def test_concurrent_appends_serialised(store: EventStore) -> None:
    threads = []
    n = 30

    def writer(i: int) -> None:
        store.append(EventKind.MESSAGE_SENT, _msg(i))

    for i in range(n):
        t = threading.Thread(target=writer, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    rows = store.read(limit=n + 5)
    assert len(rows) == n
    ids = [r.id for r in rows]
    assert ids == sorted(ids)
    assert len(set(ids)) == n
