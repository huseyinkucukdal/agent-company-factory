"""Module 14 — Board Backend API.

Public surface: :func:`build_app` returns a fully-wired FastAPI app, plus
:func:`migrate` for setting up the board-level schema. Sub-services
(:class:`AuthService`, :class:`LinkService`, :class:`AuditLog`,
:class:`BoardAPISettings`) are exposed for tests and embedding.
"""
from __future__ import annotations

from .app import build_app, migrate
from .audit import AuditEntry, AuditLog
from .auth import (
    AuthRuntime,
    AuthService,
    AuthenticatedUser,
    JWTCodec,
    TokenPair,
)
from .auth.service import BoardUser
from .exceptions import (
    BoardAPIError,
    Conflict,
    EmailAlreadyExists,
    Forbidden,
    InvalidCredentials,
    LinkAlreadyExists,
    LinkNotFound,
    NotFound,
    TokenExpired,
    TokenInvalid,
    TokenRevoked,
    Unauthorized,
    UserNotFound,
    ValidationFailed,
)
from .links import (
    Link,
    LinkRelationship,
    LinkScope,
    LinkService,
    LinkStatus,
)
from .rbac import UserRole
from .runtime import BoardRuntime
from .settings import BoardAPISettings

__all__ = [
    "AuditEntry",
    "AuditLog",
    "AuthRuntime",
    "AuthService",
    "AuthenticatedUser",
    "BoardAPIError",
    "BoardAPISettings",
    "BoardRuntime",
    "BoardUser",
    "Conflict",
    "EmailAlreadyExists",
    "Forbidden",
    "InvalidCredentials",
    "JWTCodec",
    "Link",
    "LinkAlreadyExists",
    "LinkNotFound",
    "LinkRelationship",
    "LinkScope",
    "LinkService",
    "LinkStatus",
    "NotFound",
    "TokenExpired",
    "TokenInvalid",
    "TokenPair",
    "TokenRevoked",
    "Unauthorized",
    "UserNotFound",
    "UserRole",
    "ValidationFailed",
    "build_app",
    "migrate",
]
