"""Exceptions raised by the Efficiency module."""
from __future__ import annotations


class EfficiencyError(Exception):
    """Base class for efficiency-module errors."""


class FindingNotFound(EfficiencyError):
    """Lookup of an unknown finding id."""


class InvalidTransition(EfficiencyError):
    """Attempt to drive a finding into an illegal state."""
