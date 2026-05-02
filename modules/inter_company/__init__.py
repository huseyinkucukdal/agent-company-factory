"""Module 16 — Inter-Company Communication.

Runtime cross-company message routing. Board-level link administration
(``board_links`` table + state machine) is owned by Module 14
(``modules.board_api.links``); this module only consumes that catalogue
and adds the *runtime* surface: rate-limited, scope-checked, sanitized
delivery into the target company's orchestrator, plus a Connector
service adapter so any agent can call ``inter_company.send`` like any
other external service.
"""
from __future__ import annotations

from .connector_adapter import build_inter_company_service
from .exceptions import (
    CrossRateLimited,
    DirectionMismatch,
    InterCompanyError,
    LinkNotApproved,
    LinkNotFound,
    PayloadTooLarge,
    ScopeViolation,
    TargetUnavailable,
)
from .models import (
    CrossEntry,
    CrossMessage,
    CrossMessageKind,
    CrossResult,
    CrossStatus,
)
from .service import InterCompanyService

__all__ = [
    "CrossEntry",
    "CrossMessage",
    "CrossMessageKind",
    "CrossRateLimited",
    "CrossResult",
    "CrossStatus",
    "DirectionMismatch",
    "InterCompanyError",
    "InterCompanyService",
    "LinkNotApproved",
    "LinkNotFound",
    "PayloadTooLarge",
    "ScopeViolation",
    "TargetUnavailable",
    "build_inter_company_service",
]
