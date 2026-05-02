"""Configuration for the Board Backend API.

Tests construct ``BoardAPISettings`` directly; production reads from
environment variables (``ACF_*``).
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import timedelta


def _env_seconds(key: str, default: timedelta) -> timedelta:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return timedelta(seconds=int(raw))


def _env_origins(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(key)
    if not raw:
        return default
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts or default


_DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


@dataclass(frozen=True)
class BoardAPISettings:
    """Runtime configuration for the Board API."""

    jwt_secret: str = field(
        default_factory=lambda: os.environ.get(
            "ACF_JWT_SECRET", secrets.token_urlsafe(48),
        )
    )
    jwt_algorithm: str = "HS256"
    # Access tokens default to 8 hours so a typical work day's worth of
    # browser refreshes don't kick the operator back to the login page.
    # Override with ``ACF_ACCESS_TOKEN_TTL_SECONDS`` in production.
    access_token_ttl: timedelta = field(
        default_factory=lambda: _env_seconds(
            "ACF_ACCESS_TOKEN_TTL_SECONDS", timedelta(hours=8),
        ),
    )
    refresh_token_ttl: timedelta = field(
        default_factory=lambda: _env_seconds(
            "ACF_REFRESH_TOKEN_TTL_SECONDS", timedelta(days=30),
        ),
    )
    sse_heartbeat_seconds: float = 15.0
    sse_max_backlog: int = 10_000
    cors_allow_origins: tuple[str, ...] = field(
        default_factory=lambda: _env_origins(
            "ACF_CORS_ALLOW_ORIGINS", _DEFAULT_CORS_ORIGINS,
        ),
    )


__all__ = ["BoardAPISettings"]
