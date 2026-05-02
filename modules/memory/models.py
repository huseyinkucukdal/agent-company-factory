"""Domain types exposed by the Memory subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


@dataclass(frozen=True)
class WorkingItem:
    role: str  # "user" | "agent" | "tool"
    content: str
    ts_company: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecallHit:
    kind: Kind
    item_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
