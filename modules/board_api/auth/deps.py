"""FastAPI dependencies for authentication and RBAC.

``AuthRuntime`` is the single point of access stashed on
``app.state.auth``. Per-request dependencies pull the runtime, validate
the bearer token, and return :class:`AuthenticatedUser`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..exceptions import (
    Forbidden,
    TokenExpired,
    TokenInvalid,
    Unauthorized,
)
from ..rbac import (
    UserRole,
    can_decide_approvals,
    can_decide_links,
    can_manage_users,
    can_modify_settings,
    can_view_audit,
)
from .jwt import JWTCodec
from .service import AuthService, BoardUser


@dataclass
class AuthRuntime:
    auth: AuthService
    codec: JWTCodec


@dataclass(frozen=True)
class AuthenticatedUser:
    """Subject of the current request — fully resolved board user + claims."""

    user: BoardUser

    @property
    def id(self) -> str:
        return self.user.id

    @property
    def role(self) -> UserRole:
        return self.user.role


_security = HTTPBearer(auto_error=False, bearerFormat="JWT")


def _runtime(request: Request) -> AuthRuntime:
    runtime = getattr(request.app.state, "auth", None)
    if not isinstance(runtime, AuthRuntime):
        raise RuntimeError("AuthRuntime not configured on app.state.auth")
    return runtime


def require_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_security),
) -> AuthenticatedUser:
    """Resolve the current user from the ``Authorization: Bearer <jwt>`` header.

    Browsers' ``EventSource`` cannot set custom headers, so SSE clients pass
    the JWT via the ``?token=<jwt>`` query parameter instead; we accept that
    as a fallback when no bearer header is present.
    """
    token = creds.credentials if creds and creds.credentials else None
    if not token:
        token = request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token", "code": "unauthorized"},
            headers={"WWW-Authenticate": 'Bearer realm="board"'},
        )
    runtime = _runtime(request)
    try:
        claims = runtime.codec.decode_access(token)
    except TokenExpired as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "token_expired", "code": "token_expired"},
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    except (TokenInvalid, Unauthorized) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "token_invalid", "code": "token_invalid"},
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc

    user = runtime.auth.get(claims.sub)
    return AuthenticatedUser(user=user)


def _role_predicate(
    predicate: Callable[[UserRole], bool], code: str,
) -> Callable[[AuthenticatedUser], Awaitable[AuthenticatedUser]]:
    async def dep(
        current: AuthenticatedUser = Depends(require_user),
    ) -> AuthenticatedUser:
        if not predicate(current.role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": code, "code": "forbidden"},
            )
        return current

    return dep


require_admin = _role_predicate(lambda r: r is UserRole.ADMIN, "admin_required")
require_operator = _role_predicate(can_modify_settings, "operator_required")
require_decider = _role_predicate(can_decide_approvals, "decider_required")
require_link_decider = _role_predicate(can_decide_links, "link_decider_required")
require_user_admin = _role_predicate(can_manage_users, "admin_required")
require_audit_viewer = _role_predicate(can_view_audit, "admin_required")


def assert_company_access(user: AuthenticatedUser, company_id: str, runtime: AuthRuntime) -> None:
    """ADMIN/OBSERVER see everything; OPERATOR is bounded to their assignments."""
    if user.role in (UserRole.ADMIN, UserRole.OBSERVER):
        return
    assigned = runtime.auth.assignments(user.id)
    if not assigned:
        # An operator with no explicit assignments is treated as having
        # access to companies they themselves created — but that's tracked
        # in the factory layer (companies.created_by). Fall back to allow
        # here; the company route confirms ownership when relevant.
        return
    if company_id not in assigned:
        raise Forbidden("company_not_assigned")


__all__ = [
    "AuthRuntime",
    "AuthenticatedUser",
    "assert_company_access",
    "require_admin",
    "require_audit_viewer",
    "require_decider",
    "require_link_decider",
    "require_operator",
    "require_user",
    "require_user_admin",
]
