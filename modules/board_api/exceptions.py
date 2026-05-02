"""Errors raised by Module 14 — Board Backend API."""
from __future__ import annotations


class BoardAPIError(Exception):
    """Base for all board_api errors."""

    code: str = "board_api_error"
    http_status: int = 500

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class Unauthorized(BoardAPIError):
    code = "unauthorized"
    http_status = 401


class InvalidCredentials(Unauthorized):
    code = "invalid_credentials"


class TokenExpired(Unauthorized):
    code = "token_expired"


class TokenInvalid(Unauthorized):
    code = "token_invalid"


class TokenRevoked(Unauthorized):
    code = "token_revoked"


class Forbidden(BoardAPIError):
    code = "forbidden"
    http_status = 403


class NotFound(BoardAPIError):
    code = "not_found"
    http_status = 404


class UserNotFound(NotFound):
    code = "user_not_found"


class LinkNotFound(NotFound):
    code = "link_not_found"


class Conflict(BoardAPIError):
    code = "conflict"
    http_status = 409


class EmailAlreadyExists(Conflict):
    code = "email_already_exists"


class LinkAlreadyExists(Conflict):
    code = "link_already_exists"


class ValidationFailed(BoardAPIError):
    code = "validation_failed"
    http_status = 422


class CompanyClosed(BoardAPIError):
    code = "company_closed"
    http_status = 409


__all__ = [
    "BoardAPIError",
    "CompanyClosed",
    "Conflict",
    "EmailAlreadyExists",
    "Forbidden",
    "InvalidCredentials",
    "LinkAlreadyExists",
    "LinkNotFound",
    "NotFound",
    "TokenExpired",
    "TokenInvalid",
    "TokenRevoked",
    "Unauthorized",
    "UserNotFound",
    "ValidationFailed",
]
