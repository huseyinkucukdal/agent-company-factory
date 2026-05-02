"""Authentication subsystem for the Board Backend API."""
from __future__ import annotations

from .deps import (
    AuthenticatedUser,
    AuthRuntime,
    require_admin,
    require_decider,
    require_operator,
    require_user,
)
from .jwt import JWTCodec, TokenPair
from .password import hash_password, verify_password
from .routes import build_auth_router
from .service import AuthService

__all__ = [
    "AuthRuntime",
    "AuthService",
    "AuthenticatedUser",
    "JWTCodec",
    "TokenPair",
    "build_auth_router",
    "hash_password",
    "require_admin",
    "require_decider",
    "require_operator",
    "require_user",
    "verify_password",
]
