"""Protocols Storage depends on but does not implement.

Other modules (Identity, Event Store) provide concrete implementations.
Tests use the fakes in `tests/fakes.py`.
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IdentityProvider(Protocol):
    """Resolves whether a reader may access another agent's workspace."""

    def can_read_workspace(self, reader_id: str, owner_id: str) -> bool: ...


@runtime_checkable
class EventSink(Protocol):
    """Where Storage publishes notable events (quota warnings, drift, ...)."""

    def emit(self, kind: str, payload: dict[str, Any]) -> None: ...
