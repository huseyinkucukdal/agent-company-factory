"""Default timeouts and security-veto policy per :class:`ApprovalKind`."""
from __future__ import annotations

from datetime import timedelta

from .models import ApprovalKind

DEFAULT_TIMEOUTS: dict[ApprovalKind, timedelta] = {
    ApprovalKind.HIRE: timedelta(days=7),
    ApprovalKind.FIRE_DEPTH_1: timedelta(days=7),
    ApprovalKind.EXPENSE: timedelta(hours=24),
    ApprovalKind.EXTERNAL_ACTION: timedelta(hours=24),
    ApprovalKind.INTER_COMPANY_LINK: timedelta(days=30),
    ApprovalKind.SECURITY_FLAG: timedelta(minutes=5),
    ApprovalKind.BUDGET_INCREASE: timedelta(days=7),
    ApprovalKind.ROLE_CHANGE: timedelta(days=7),
}

# Kinds that auto-DENY (instead of TIMEOUT) on expiry — SECURITY_FLAG closes
# fail-secure: if no human responds in time, the action is refused.
AUTO_DENY_ON_TIMEOUT: frozenset[ApprovalKind] = frozenset(
    {ApprovalKind.SECURITY_FLAG}
)

# Kinds that, by default, require a security pre-veto.
DEFAULT_REQUIRE_SECURITY: frozenset[ApprovalKind] = frozenset(
    {ApprovalKind.EXPENSE, ApprovalKind.EXTERNAL_ACTION}
)
