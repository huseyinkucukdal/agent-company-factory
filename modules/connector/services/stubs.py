"""Phase-1 stub services.

Each builder returns a registered :class:`ServiceDef` whose executors raise
:class:`NotImplementedError` — they exist so the allowlist + approval routing
can be tested ahead of real integrations.
"""
from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from modules.cost import Money

from ..models import ActionDef, AuthMethod, RiskLevel, ServiceDef


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _StubArgs(_Frozen):
    note: str = Field(default="")


class _StubResult(_Frozen):
    accepted: bool


def _stub_executor(_args: _StubArgs) -> _StubResult:
    raise NotImplementedError("phase-1 stub")


def _stub_sanitizer(value: _StubResult) -> _StubResult:
    if not isinstance(value, _StubResult):
        raise ValueError("expected _StubResult")
    return value


def _stub_cost(amount: str) -> Callable[[_StubArgs], Money]:
    def _est(_args: _StubArgs) -> Money:
        return Money.of(Decimal(amount))

    return _est


def _service(
    name: str, description: str, risk: RiskLevel, actions: list[str], cost: str
) -> ServiceDef:
    action_map: dict[str, ActionDef] = {
        action: ActionDef(
            description=f"{description} — {action}",
            args_schema=_StubArgs,
            output_schema=_StubResult,
            cost_estimator=_stub_cost(cost),
            executor=_stub_executor,
            sanitizer=_stub_sanitizer,
        )
        for action in actions
    }
    return ServiceDef(
        name=name, description=description,
        auth=AuthMethod.API_KEY, risk=risk, actions=action_map,
    )


def build_domain_service() -> ServiceDef:
    return _service(
        "domain", "Domain registrar", RiskLevel.HIGH,
        ["register", "renew", "transfer"], "10.00",
    )


def build_hosting_service() -> ServiceDef:
    return _service(
        "hosting", "Cloud hosting", RiskLevel.HIGH,
        ["create_droplet", "destroy_droplet", "list_droplets"], "5.00",
    )


def build_payment_service() -> ServiceDef:
    return _service(
        "payment", "Payment processor", RiskLevel.CRITICAL,
        ["charge", "refund", "payout"], "1.00",
    )


def build_ads_service() -> ServiceDef:
    return _service(
        "ads", "Ad platform", RiskLevel.HIGH,
        ["create_campaign", "pause_campaign", "fetch_metrics"], "5.00",
    )


def build_social_service() -> ServiceDef:
    return _service(
        "social", "Social posting", RiskLevel.MEDIUM,
        ["post", "delete"], "0.50",
    )


__all__ = [
    "build_ads_service",
    "build_domain_service",
    "build_hosting_service",
    "build_payment_service",
    "build_social_service",
]
