"""Module 06 — Approval System.

Public surface: a per-company approval queue with security pre-veto, real-time
timeouts, and idempotent state transitions.
"""
from __future__ import annotations

from .exceptions import (
    ApprovalConflict,
    ApprovalError,
    ApprovalNotFound,
    InvalidTransition,
    UnauthorizedDecider,
)
from .models import (
    Approval,
    ApprovalKind,
    ApprovalRoute,
    ApprovalStatus,
    Decision,
    RouteTarget,
    SubscriptionHandle,
)
from .routing import (
    AUTO_DENY_ON_TIMEOUT,
    DEFAULT_REQUIRE_SECURITY,
    DEFAULT_TIMEOUTS,
)
from .service import Approvals

__all__ = [
    "AUTO_DENY_ON_TIMEOUT",
    "DEFAULT_REQUIRE_SECURITY",
    "DEFAULT_TIMEOUTS",
    "Approval",
    "ApprovalConflict",
    "ApprovalError",
    "ApprovalKind",
    "ApprovalNotFound",
    "ApprovalRoute",
    "ApprovalStatus",
    "Approvals",
    "Decision",
    "InvalidTransition",
    "RouteTarget",
    "SubscriptionHandle",
    "UnauthorizedDecider",
]
