"""Domain models for Module 16 — Inter-Company Communication."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class CrossMessageKind(StrEnum):
    INQUIRY = "inquiry"
    QUOTE = "quote"
    ORDER = "order"
    INVOICE = "invoice"
    DELIVERY = "delivery"
    GENERIC = "generic"


@dataclass(frozen=True)
class CrossMessage:
    """Payload an agent ships across a link."""

    kind: CrossMessageKind
    subject: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


class CrossStatus(StrEnum):
    DELIVERED = "delivered"
    QUEUED = "queued"      # target paused — will be retried by deliver_pending
    REJECTED = "rejected"  # scope/rate/target failure


@dataclass(frozen=True)
class CrossResult:
    """Outcome of a single ``send_cross`` call."""

    id: str
    link_id: str
    status: CrossStatus
    target_agent: str | None
    reason: str | None = None


@dataclass(frozen=True)
class CrossEntry:
    """Persisted outbox entry (board DB)."""

    id: str
    link_id: str
    from_company: str
    to_company: str
    from_agent: str
    target_agent: str | None
    kind: CrossMessageKind
    subject: str
    body: str
    metadata: dict[str, Any]
    status: CrossStatus
    reason: str | None
    created_at: datetime
    delivered_at: datetime | None


__all__ = [
    "CrossEntry",
    "CrossMessage",
    "CrossMessageKind",
    "CrossResult",
    "CrossStatus",
]
