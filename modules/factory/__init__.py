"""Module 13 — Company Factory.

Public surface: :class:`CompanyFactory` plus the data classes that
describe a company spec and its post-bootstrap handle.
"""
from __future__ import annotations

from .bootstrap import FactoryConfig, LLMFactory
from .exceptions import (
    BootstrapFailed,
    CompanyAlreadyExists,
    CompanyNotFound,
    FactoryError,
    InvalidSpec,
    ShutdownTimeout,
)
from .factory import CompanyFactory
from .handle import CompanyHandle
from .spec import (
    CompanySpec,
    CompanyStatus,
    CompanySummary,
    ExtraAgentSpec,
)

__all__ = [
    "BootstrapFailed",
    "CompanyAlreadyExists",
    "CompanyFactory",
    "CompanyHandle",
    "CompanyNotFound",
    "CompanySpec",
    "CompanyStatus",
    "CompanySummary",
    "ExtraAgentSpec",
    "FactoryConfig",
    "FactoryError",
    "InvalidSpec",
    "LLMFactory",
    "ShutdownTimeout",
]
