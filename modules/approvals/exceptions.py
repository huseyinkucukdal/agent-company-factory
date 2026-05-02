"""Approval-system exception hierarchy."""
from __future__ import annotations


class ApprovalError(Exception):
    """Base class for approval-system errors."""


class ApprovalNotFound(ApprovalError):
    """Lookup of an unknown ``request_id``."""


class InvalidTransition(ApprovalError):
    """Attempt to drive an approval into an illegal state."""


class UnauthorizedDecider(ApprovalError):
    """Decider does not match the route's allowed party."""


class ApprovalConflict(ApprovalError):
    """Attempt to re-decide an already-decided approval with a conflicting decision."""
