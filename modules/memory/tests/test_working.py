"""Working-memory layer tests."""
from __future__ import annotations

import threading

from modules.memory import Memory, WorkingItem

from .conftest import now


def _item(role: str, content: str) -> WorkingItem:
    return WorkingItem(role=role, content=content, ts_company=now())


def test_append_and_read_working(memory: Memory) -> None:
    memory.append_working("a", _item("user", "hi"))
    memory.append_working("a", _item("agent", "hello"))
    out = memory.working_window("a")
    assert [w.content for w in out] == ["hi", "hello"]


def test_working_window_returns_last_n(memory: Memory) -> None:
    for i in range(5):
        memory.append_working("a", _item("agent", f"msg-{i}"))
    # Default working_size in fixture = 3 → last three.
    out = memory.working_window("a")
    assert [w.content for w in out] == ["msg-2", "msg-3", "msg-4"]
    # Explicit override returns all.
    full = memory.working_window("a", n=100)
    assert len(full) == 5


def test_clear_working(memory: Memory) -> None:
    memory.append_working("a", _item("user", "x"))
    memory.clear_working("a")
    assert memory.working_window("a") == []


def test_working_isolated_per_agent(memory: Memory) -> None:
    memory.append_working("a", _item("user", "for-a"))
    memory.append_working("b", _item("user", "for-b"))
    assert [w.content for w in memory.working_window("a")] == ["for-a"]
    assert [w.content for w in memory.working_window("b")] == ["for-b"]


def test_concurrent_appends_no_id_collision(memory: Memory) -> None:
    n_threads = 8
    per_thread = 5
    barrier = threading.Barrier(n_threads)

    def worker(tag: str) -> None:
        barrier.wait()
        for i in range(per_thread):
            memory.append_working(
                "a", _item("agent", f"{tag}-{i}")
            )

    threads = [
        threading.Thread(target=worker, args=(f"t{i}",))
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    items = memory.working_window("a", n=10_000)
    assert len(items) == n_threads * per_thread
