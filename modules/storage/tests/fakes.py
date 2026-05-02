"""In-memory fakes for the protocols Storage depends on."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeIdentity:
    """`IdentityProvider` fake. ``manages[(reader, owner)] = True`` allows reads."""

    manages: dict[tuple[str, str], bool] = field(default_factory=dict)

    def can_read_workspace(self, reader_id: str, owner_id: str) -> bool:
        if reader_id == owner_id:
            return True
        return self.manages.get((reader_id, owner_id), False)


@dataclass
class FakeEventSink:
    """`EventSink` fake that records emitted events."""

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]
