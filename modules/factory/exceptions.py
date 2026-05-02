"""Errors raised by Module 13 — Company Factory."""
from __future__ import annotations


class FactoryError(Exception):
    """Base class for all factory errors."""

    code: str = "factory_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class BootstrapFailed(FactoryError):
    """`create_company` could not finish; rollback was attempted."""

    code = "bootstrap_failed"


class CompanyAlreadyExists(FactoryError):
    """Caller asked for a company id that already exists in the board DB."""

    code = "company_already_exists"


class CompanyNotFound(FactoryError):
    """No company with the given id is registered in the board DB."""

    code = "company_not_found"


class ShutdownTimeout(FactoryError):
    """`close_company` could not reach idle within the deadline."""

    code = "shutdown_timeout"


class InvalidSpec(FactoryError):
    """`CompanySpec` failed validation."""

    code = "invalid_spec"


__all__ = [
    "BootstrapFailed",
    "CompanyAlreadyExists",
    "CompanyNotFound",
    "FactoryError",
    "InvalidSpec",
    "ShutdownTimeout",
]
