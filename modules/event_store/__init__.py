"""Event Store: append-only audit log + live feed + replay source.

See ``PLAN.md`` for the full design.
"""
from .exceptions import (
    EventStoreError,
    PayloadTooLarge,
    PayloadValidationError,
    UnknownEventKind,
)
from .kinds import EventKind
from .store import Event, EventStore, SubscriptionHandle

__all__ = [
    "Event",
    "EventKind",
    "EventStore",
    "EventStoreError",
    "PayloadTooLarge",
    "PayloadValidationError",
    "SubscriptionHandle",
    "UnknownEventKind",
]
