"""In-memory implementation of :class:`MessageQueue`.

Used in dev and unit tests. The shape mirrors Redis Streams (FIFO per
agent-key plus a shared dead-letter queue), so a Redis adapter can plug in
later without changing the Orchestrator.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime
from typing import cast

from .models import DeadLetter, MessageEnvelope


class InMemoryMessageQueue:
    """Thread-unsafe FIFO buckets keyed by ``agent_id``."""

    def __init__(self) -> None:
        self._queues: dict[str, deque[MessageEnvelope]] = {}
        self._seen: dict[str, set[str]] = {}
        self._dlq: list[DeadLetter] = []
        self._available = True
        self._waiters: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------- helpers

    def _bucket(self, agent_id: str) -> deque[MessageEnvelope]:
        return self._queues.setdefault(agent_id, deque())

    def _waiter(self, agent_id: str) -> asyncio.Event:
        ev = self._waiters.get(agent_id)
        if ev is None:
            ev = asyncio.Event()
            self._waiters[agent_id] = ev
        return ev

    # -------------------------------------------------------------- public

    def is_available(self) -> bool:
        return self._available

    def set_available(self, value: bool) -> None:
        """Test hook: simulate Redis going down."""
        self._available = value

    def push(self, envelope: MessageEnvelope) -> None:
        if not self._available:
            from .exceptions import RedisUnavailable

            raise RedisUnavailable("queue offline")
        self._bucket(envelope.to_agent).append(envelope)
        if envelope.correlation_id:
            self._seen.setdefault(envelope.to_agent, set()).add(
                envelope.correlation_id
            )
        self._waiter(envelope.to_agent).set()

    def pop(self, agent_id: str) -> MessageEnvelope | None:
        bucket = self._bucket(agent_id)
        if not bucket:
            return None
        env = bucket.popleft()
        if not bucket:
            self._waiter(agent_id).clear()
        return env

    def pending_count(self, agent_id: str) -> int:
        return len(self._bucket(agent_id))

    def has_correlation(self, agent_id: str, correlation_id: str) -> bool:
        return correlation_id in self._seen.get(agent_id, set())

    def to_dlq(
        self, envelope: MessageEnvelope, reason: str, *, now: object
    ) -> None:
        self._dlq.append(
            DeadLetter(
                envelope=envelope,
                reason=reason,
                moved_at=cast(datetime, now),
            )
        )

    def dlq(self) -> list[DeadLetter]:
        return list(self._dlq)

    def aiter(self, agent_id: str):  # type: ignore[no-untyped-def]
        """Yield envelopes as they arrive — used by background consumers."""
        async def _gen() -> AsyncIterator[MessageEnvelope]:
            while True:
                env = self.pop(agent_id)
                if env is not None:
                    yield env
                    continue
                await self._waiter(agent_id).wait()
        return _gen()


__all__ = ["InMemoryMessageQueue"]
