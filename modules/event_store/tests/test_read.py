"""Tests for ``EventStore.read``."""
from __future__ import annotations

from modules.event_store import EventKind, EventStore


def _seed(store: EventStore) -> None:
    store.append(
        EventKind.MESSAGE_SENT,
        {"from_agent": "a", "to_agent": "b", "content": "1"},
        actor="a",
        correlation="task-1",
    )
    store.append(
        EventKind.MESSAGE_SENT,
        {"from_agent": "b", "to_agent": "a", "content": "2"},
        actor="b",
        correlation="task-1",
    )
    store.append(
        EventKind.TOOL_CALLED,
        {"tool": "shell", "arguments": {}, "request_id": "r1"},
        actor="a",
        correlation="task-2",
    )
    store.append(
        EventKind.TOOL_RESULT,
        {"tool": "shell", "request_id": "r1", "ok": True, "result": {"x": 1}},
        actor="a",
        correlation="task-2",
    )


def test_read_filter_by_kind(store: EventStore) -> None:
    _seed(store)
    rows = store.read(kinds=[EventKind.TOOL_CALLED])
    assert len(rows) == 1
    assert rows[0].kind is EventKind.TOOL_CALLED


def test_read_filter_by_actor(store: EventStore) -> None:
    _seed(store)
    rows = store.read(actor="b")
    assert len(rows) == 1
    assert rows[0].actor_agent_id == "b"


def test_read_since_returns_only_newer(store: EventStore) -> None:
    _seed(store)
    first = store.read(limit=1)[0]
    rest = store.read(since=first.id)
    assert all(r.id > first.id for r in rest)
    assert len(rest) == 3


def test_replay_until_works(store: EventStore) -> None:
    _seed(store)
    all_rows = store.read()
    cutoff = all_rows[1].id
    rows = store.read(until=cutoff)
    assert [r.id for r in rows] == [all_rows[0].id, all_rows[1].id]


def test_correlation_grouping(store: EventStore) -> None:
    _seed(store)
    rows = store.read(correlation="task-2")
    assert {r.kind for r in rows} == {
        EventKind.TOOL_CALLED,
        EventKind.TOOL_RESULT,
    }
    assert all(r.correlation_id == "task-2" for r in rows)


def test_read_combined_filters(store: EventStore) -> None:
    _seed(store)
    rows = store.read(
        kinds=[EventKind.MESSAGE_SENT, EventKind.TOOL_RESULT],
        actor="a",
    )
    assert {r.kind for r in rows} == {
        EventKind.MESSAGE_SENT,
        EventKind.TOOL_RESULT,
    }
    assert all(r.actor_agent_id == "a" for r in rows)
