"""Protocols the Orchestrator depends on.

These match the duck-typed surfaces of :class:`modules.agent_runtime.Agent`
and the in-memory message queue, so the production Redis-backed adapter can
slot in later without touching :mod:`modules.orchestrator.orchestrator`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from modules.agent_runtime import AgentStatus, HealthSignals, IncomingMessage

from .models import DeadLetter, MessageEnvelope


@runtime_checkable
class AgentHandle(Protocol):
    """Subset of the :class:`Agent` API the Orchestrator needs."""

    @property
    def id(self) -> str: ...
    @property
    def inbox_size(self) -> int: ...
    def status(self) -> AgentStatus: ...
    def health_signals(self) -> HealthSignals: ...
    async def deliver(self, message: IncomingMessage) -> None: ...


@runtime_checkable
class MessageQueue(Protocol):
    """Backend-agnostic stream API.

    Implementations: :class:`InMemoryMessageQueue` (default, dev/tests) and
    a future Redis Streams adapter.
    """

    def push(self, envelope: MessageEnvelope) -> None: ...
    def pop(self, agent_id: str) -> MessageEnvelope | None: ...
    def pending_count(self, agent_id: str) -> int: ...
    def has_correlation(self, agent_id: str, correlation_id: str) -> bool: ...
    def to_dlq(self, envelope: MessageEnvelope, reason: str, *, now: object) -> None: ...
    def dlq(self) -> list[DeadLetter]: ...
    def aiter(self, agent_id: str) -> AsyncIterator[MessageEnvelope]: ...
    def is_available(self) -> bool: ...

__all__ = ["AgentHandle", "MessageQueue"]
