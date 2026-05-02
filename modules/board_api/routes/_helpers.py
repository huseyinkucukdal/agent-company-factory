"""Shared helpers used by the route modules — audit shortcut, error mapping,
and a tiny adapter from internal exceptions to ``HTTPException``.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from ..audit import AuditLog
from ..auth.deps import AuthenticatedUser, AuthRuntime, assert_company_access
from ..exceptions import BoardAPIError
from ..runtime import BoardRuntime


def client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def audit_record(
    audit: AuditLog,
    *,
    user_id: str,
    action: str,
    request: Request,
    target: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    ip, ua = client_meta(request)
    audit.record(
        user_id=user_id, action=action, target=target,
        payload=payload or {}, ip=ip, user_agent=ua,
    )


def http_error(exc: BoardAPIError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={"error": str(exc) or exc.code, "code": exc.code},
    )


def ensure_company_access(
    runtime: BoardRuntime, user: AuthenticatedUser, company_id: str,
) -> None:
    try:
        assert_company_access(user, company_id, runtime.auth)
    except BoardAPIError as exc:
        raise http_error(exc) from exc


def not_found(detail: str, code: str = "not_found") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": detail, "code": code},
    )


def conflict(detail: str, code: str = "conflict") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": detail, "code": code},
    )


def validation_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": detail, "code": "validation_failed"},
    )


__all__ = [
    "audit_record",
    "client_meta",
    "conflict",
    "ensure_company_access",
    "http_error",
    "not_found",
    "validation_error",
]
