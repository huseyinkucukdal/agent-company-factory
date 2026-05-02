"""Service builder smoke tests."""
from __future__ import annotations

from modules.connector import RiskLevel
from modules.connector.services import (
    build_ads_service,
    build_domain_service,
    build_email_service,
    build_hosting_service,
    build_http_service,
    build_payment_service,
    build_social_service,
)


def test_http_definition(http_client: object) -> None:
    svc = build_http_service(http_client)  # type: ignore[arg-type]
    assert svc.name == "http"
    assert svc.risk is RiskLevel.MEDIUM
    assert {"get", "post", "put", "delete"} <= set(svc.actions)


def test_email_definition(email_client: object) -> None:
    svc = build_email_service(email_client, sender="x@y")  # type: ignore[arg-type]
    assert svc.name == "email"
    assert "send" in svc.actions
    assert "send_with_attachment" in svc.actions


def test_payment_is_critical() -> None:
    assert build_payment_service().risk is RiskLevel.CRITICAL


def test_high_risk_stubs() -> None:
    for svc in (
        build_domain_service(),
        build_hosting_service(),
        build_ads_service(),
    ):
        assert svc.risk is RiskLevel.HIGH


def test_social_is_medium() -> None:
    assert build_social_service().risk is RiskLevel.MEDIUM
