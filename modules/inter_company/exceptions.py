"""Errors raised by the Inter-Company runtime."""
from __future__ import annotations


class InterCompanyError(Exception):
    """Base for all inter-company runtime errors."""


class LinkNotFound(InterCompanyError):
    """The supplied ``link_id`` does not exist in the board catalogue."""


class LinkNotApproved(InterCompanyError):
    """Link exists but is not in APPROVED state (denied/suspended/revoked)."""


class DirectionMismatch(InterCompanyError):
    """``from_company`` does not match ``link.from_company``.

    Links are directional. A separate link must be requested for the
    reverse direction.
    """


class ScopeViolation(InterCompanyError):
    """Message kind is not in ``link.scope.allowed_messages``."""


class PayloadTooLarge(InterCompanyError):
    """Body exceeds ``link.scope.max_payload_bytes``."""


class CrossRateLimited(InterCompanyError):
    """Per-link hourly throughput exceeded."""


class TargetUnavailable(InterCompanyError):
    """Target company is not loaded (closed or never instantiated)."""


__all__ = [
    "CrossRateLimited",
    "DirectionMismatch",
    "InterCompanyError",
    "LinkNotApproved",
    "LinkNotFound",
    "PayloadTooLarge",
    "ScopeViolation",
    "TargetUnavailable",
]
