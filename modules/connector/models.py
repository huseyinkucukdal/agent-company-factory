"""Domain types for the Connector."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from modules.cost import Money


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuthMethod(StrEnum):
    NONE = "none"
    API_KEY = "api_key"
    OAUTH = "oauth"


@dataclass(frozen=True)
class RateLimit:
    """Token-bucket configuration."""

    capacity: int
    refill_per_second: float


@dataclass(frozen=True)
class ActionDef:
    description: str
    args_schema: type[BaseModel]
    output_schema: type[BaseModel]
    cost_estimator: Callable[..., Money]
    executor: Callable[..., Awaitable[Any] | Any]
    sanitizer: Callable[..., BaseModel]
    rate_limit: RateLimit | None = None
    auto_approve_threshold: Money | None = None
    idempotent: bool = True


@dataclass(frozen=True)
class ServiceDef:
    name: str
    description: str
    auth: AuthMethod
    risk: RiskLevel
    actions: dict[str, ActionDef]


@dataclass(frozen=True)
class ConnectorResult:
    ok: bool
    output: BaseModel | None = None
    error_code: str | None = None
    error_message: str | None = None
    cost_usd: Decimal = Decimal("0.0000")
    approval_request_id: str | None = None
