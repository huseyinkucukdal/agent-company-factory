"""Exceptions raised by the Clock module."""


class ClockError(Exception):
    """Base for Clock errors."""


class InvalidStateTransition(ClockError):
    """A pause/resume call is not legal from the current state."""
