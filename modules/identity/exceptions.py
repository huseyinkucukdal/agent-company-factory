"""Identity exceptions."""
from __future__ import annotations


class IdentityError(Exception):
    """Base for all identity-module errors."""


class FireDenied(IdentityError):
    """A fire request violated authorisation or routing rules."""


class HireDenied(IdentityError):
    """A hire request violated routing or invariants."""


class CycleDetected(IdentityError):
    """An ``add_agent`` call would introduce a cycle in the org tree."""


class SpecialRoleProtected(IdentityError):
    """Attempt to remove a CEO/HR/Security without proper handling."""
