"""Built-in service definitions.

Phase-1 ships only mock executors. Each service's executor accepts a
``HttpClient`` / ``EmailClient`` Protocol so tests can swap in fakes.
External hardening (TLS pinning, real DNS allowlisting, audit-grade logging)
arrives in a later module.
"""
from __future__ import annotations

from .email_service import build_email_service
from .http_service import build_http_service
from .stubs import (
    build_ads_service,
    build_domain_service,
    build_hosting_service,
    build_payment_service,
    build_social_service,
)

__all__ = [
    "build_ads_service",
    "build_domain_service",
    "build_email_service",
    "build_hosting_service",
    "build_http_service",
    "build_payment_service",
    "build_social_service",
]
