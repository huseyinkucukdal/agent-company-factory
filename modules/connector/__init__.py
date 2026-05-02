"""Connector — single-doorway gateway to external services."""
from __future__ import annotations

from .allowlist import Allowlist
from .connector import Connector
from .exceptions import (
    ApprovalDeniedError,
    ConnectorError,
    ExternalServiceFailure,
    MalformedResponse,
    MissingCredential,
    PendingApprovalError,
    RateLimited,
    ServiceNotAllowed,
    UnknownAction,
    UnknownService,
)
from .models import (
    ActionDef,
    AuthMethod,
    ConnectorResult,
    RateLimit,
    RiskLevel,
    ServiceDef,
)
from .rate_limit import RateLimiter
from .sanitizer import flag_injection_keys, safe_text
from .secrets import EncryptedSqliteSecrets, Secrets, in_memory_secrets

__all__ = [
    "ActionDef",
    "Allowlist",
    "ApprovalDeniedError",
    "AuthMethod",
    "Connector",
    "ConnectorError",
    "ConnectorResult",
    "EncryptedSqliteSecrets",
    "ExternalServiceFailure",
    "MalformedResponse",
    "MissingCredential",
    "PendingApprovalError",
    "RateLimit",
    "RateLimited",
    "RateLimiter",
    "RiskLevel",
    "Secrets",
    "ServiceDef",
    "ServiceNotAllowed",
    "UnknownAction",
    "UnknownService",
    "flag_injection_keys",
    "in_memory_secrets",
    "safe_text",
]
