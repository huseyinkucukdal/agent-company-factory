"""External-collaborator Protocols. The Tool Layer depends on these abstract
shapes; concrete services (``modules.identity.Org`` etc.) satisfy them."""
from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol

from modules.cost import ChargeResult, Money, Reservation
from modules.identity import Agent, Role


class IdentityProvider(Protocol):
    def get(self, agent_id: str) -> Agent: ...

    def can_read_workspace(
        self, reader_id: str, owner_id: str
    ) -> bool: ...


class CostProvider(Protocol):
    def reserve(self, amount: Money, ref: str) -> Reservation: ...

    def commit(
        self, reservation_id: str, actual_amount: Money
    ) -> ChargeResult: ...

    def release(self, reservation_id: str) -> None: ...


class ApprovalsProvider(Protocol):
    def request(
        self,
        *,
        kind: Any,
        requester_id: str,
        payload: dict[str, Any],
        route: Any,
        request_id: str | None = None,
    ) -> Any: ...

    def get(self, request_id: str) -> Any: ...


class WorkspaceProvider(Protocol):
    def write(
        self, agent_id: str, relative_path: str, data: bytes
    ) -> Any: ...

    def read_own(self, agent_id: str, relative_path: str) -> bytes: ...

    def read_subordinate(
        self, reader_id: str, owner_id: str, relative_path: str
    ) -> bytes: ...

    def list(self, agent_id: str, relative_path: str = "") -> list[Any]: ...


class MemoryProvider(Protocol):
    def remember_fact(
        self,
        agent_id: str,
        key: str,
        value: str,
        *,
        embed: bool = True,
    ) -> str: ...

    def remember_episode(
        self,
        agent_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    def recall(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
        *,
        kinds: list[Any] | None = None,
        for_reader: str | None = None,
    ) -> list[Any]: ...


class EventSink(Protocol):
    def append(
        self,
        kind: Any,
        payload: dict[str, Any],
        *,
        actor: str | None = None,
        correlation: str | None = None,
    ) -> Any: ...


class Connector(Protocol):
    def call(
        self, service: str, endpoint: str, args: dict[str, Any]
    ) -> Awaitable[Any]: ...


__all__ = [
    "ApprovalsProvider",
    "Connector",
    "CostProvider",
    "EventSink",
    "IdentityProvider",
    "MemoryProvider",
    "Role",
    "WorkspaceProvider",
]
