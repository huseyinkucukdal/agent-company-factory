"""Exceptions raised by the Event Store module."""


class EventStoreError(Exception):
    """Base for all Event Store errors."""


class UnknownEventKind(EventStoreError):
    """The given ``kind`` is not a member of :class:`EventKind`."""


class PayloadTooLarge(EventStoreError):
    """The serialised JSON payload exceeded the configured limit."""


class PayloadValidationError(EventStoreError):
    """The payload failed schema validation for its event kind."""
