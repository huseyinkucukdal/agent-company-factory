"""Errors raised by the Security Agent (Module 12)."""
from __future__ import annotations


class SecurityError(Exception):
    """Base class for security-policy faults."""

    code: str = "security_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class PolicyViolation(SecurityError):
    """A request is rejected by a hard policy rule."""

    code = "policy_violation"


__all__ = ["PolicyViolation", "SecurityError"]
