"""Module 04 — Identity & Org Chart.

Public surface: agent CRUD, the org tree, hire/fire flow with HR routing,
and workspace read-permission judgement consumed by Storage.
"""
from __future__ import annotations

from .exceptions import (
    CycleDetected,
    FireDenied,
    HireDenied,
    IdentityError,
    SpecialRoleProtected,
)
from .models import Agent, ApprovalDecision, FireResult, Role, Status
from .org import (
    MAX_ACTIVE_AGENTS,
    MAX_CEO_DIRECT_REPORTS,
    MAX_MANAGER_DIRECT_REPORTS,
    Org,
)
from .protocols import ApprovalRequester

__all__ = [
    "Agent",
    "ApprovalDecision",
    "ApprovalRequester",
    "CycleDetected",
    "FireDenied",
    "FireResult",
    "HireDenied",
    "IdentityError",
    "MAX_ACTIVE_AGENTS",
    "MAX_CEO_DIRECT_REPORTS",
    "MAX_MANAGER_DIRECT_REPORTS",
    "Org",
    "Role",
    "SpecialRoleProtected",
    "Status",
]
