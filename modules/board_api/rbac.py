"""Role-based access control.

`UserRole` and the policy helpers are pure data — no DB, no IO, easy to
unit-test. The FastAPI dependency that consumes them lives in
``auth/deps.py``.
"""
from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    OBSERVER = "observer"


def can_decide_approvals(role: UserRole) -> bool:
    return role in (UserRole.ADMIN, UserRole.OPERATOR)


def can_modify_settings(role: UserRole) -> bool:
    return role in (UserRole.ADMIN, UserRole.OPERATOR)


def can_create_company(role: UserRole) -> bool:
    return role in (UserRole.ADMIN, UserRole.OPERATOR)


def can_close_company(role: UserRole) -> bool:
    return role in (UserRole.ADMIN, UserRole.OPERATOR)


def can_manage_users(role: UserRole) -> bool:
    return role is UserRole.ADMIN


def can_view_audit(role: UserRole) -> bool:
    return role is UserRole.ADMIN


def can_read_workspaces(role: UserRole) -> bool:
    return role in (UserRole.ADMIN, UserRole.OPERATOR)


def can_decide_links(role: UserRole) -> bool:
    return role is UserRole.ADMIN


__all__ = [
    "UserRole",
    "can_close_company",
    "can_create_company",
    "can_decide_approvals",
    "can_decide_links",
    "can_manage_users",
    "can_modify_settings",
    "can_read_workspaces",
    "can_view_audit",
]
