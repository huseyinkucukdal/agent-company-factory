"""End-to-end Connector pipeline tests."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from modules.approvals import Approvals, Decision
from modules.connector import (
    ActionDef,
    Allowlist,
    AuthMethod,
    Connector,
    RateLimit,
    RiskLevel,
    ServiceDef,
)
from modules.connector.exceptions import ExternalServiceFailure
from modules.connector.services import build_email_service, build_http_service
from modules.cost import Money
from modules.event_store import EventKind, EventStore


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ----- helpers ---------------------------------------------------------------


class _Args(_Frozen):
    text: str = ""


class _Result(_Frozen):
    echo: str


def _build_service(
    name: str, risk: RiskLevel, *,
    cost: str = "0.0010",
    auto_threshold: str | None = "0.0050",
    rate: RateLimit | None = None,
    fail: bool = False,
    fail_first: int = 0,
) -> ServiceDef:
    state = {"failures": 0}

    def _executor(args: _Args) -> _Result:
        if state["failures"] < fail_first:
            state["failures"] += 1
            raise ExternalServiceFailure("transient")
        if fail:
            raise ExternalServiceFailure("permanent")
        return _Result(echo=args.text)

    threshold = Money.of(Decimal(auto_threshold)) if auto_threshold else None
    return ServiceDef(
        name=name,
        description=f"test {name}",
        auth=AuthMethod.NONE,
        risk=risk,
        actions={
            "call": ActionDef(
                description="echo",
                args_schema=_Args,
                output_schema=_Result,
                cost_estimator=lambda _a: Money.of(Decimal(cost)),
                executor=_executor,
                sanitizer=lambda v: v if isinstance(v, _Result) else _Result(echo=""),
                rate_limit=rate,
                auto_approve_threshold=threshold,
                idempotent=True,
            )
        },
    )


# ----- 1: disallowed service is denied ---------------------------------------


@pytest.mark.asyncio
async def test_disallowed_service_denied(connector: Connector) -> None:
    connector.register_service(_build_service("svc1", RiskLevel.LOW))
    # allowlist intentionally empty
    res = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "hi"}, correlation_id="c1",
    )
    assert res.ok is False
    assert res.error_code == "not_allowed"


# ----- 2: unknown service / action -------------------------------------------


@pytest.mark.asyncio
async def test_unknown_service(connector: Connector) -> None:
    res = await connector.call(
        agent_id="a1", service="ghost", action="call",
        args={}, correlation_id="c1",
    )
    assert res.ok is False
    assert res.error_code == "unknown_service"


@pytest.mark.asyncio
async def test_unknown_action(connector: Connector, allowlist: Allowlist) -> None:
    connector.register_service(_build_service("svc1", RiskLevel.LOW))
    allowlist.allow("svc1")
    res = await connector.call(
        agent_id="a1", service="svc1", action="missing",
        args={}, correlation_id="c1",
    )
    assert res.ok is False
    assert res.error_code == "unknown_action"


# ----- 3: low + under threshold → auto-approved ------------------------------


@pytest.mark.asyncio
async def test_low_risk_under_threshold_auto_approved(
    connector: Connector, allowlist: Allowlist,
) -> None:
    connector.register_service(_build_service("svc1", RiskLevel.LOW))
    allowlist.allow("svc1", "call")
    res = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "hello"}, correlation_id="c1",
    )
    assert res.ok is True
    assert isinstance(res.output, _Result)
    assert res.output.echo == "hello"
    assert res.cost_usd == Decimal("0.0010")


# ----- 4: medium-risk routes to approval -------------------------------------


@pytest.mark.asyncio
async def test_medium_risk_routes_to_approval(
    connector: Connector, allowlist: Allowlist, approvals: Approvals,
) -> None:
    connector.register_service(_build_service("svc1", RiskLevel.MEDIUM))
    allowlist.allow("svc1")
    res = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "x"}, correlation_id="c1",
    )
    assert res.ok is False
    assert res.error_code == "pending_approval"
    assert res.approval_request_id == "c1"
    pending = approvals.get("c1")
    assert pending.payload["risk"] == "medium"


# ----- 5: critical risk forces security pre-veto -----------------------------


@pytest.mark.asyncio
async def test_critical_risk_requires_security(
    connector: Connector, allowlist: Allowlist, approvals: Approvals,
) -> None:
    connector.register_service(_build_service("svc1", RiskLevel.CRITICAL))
    allowlist.allow("svc1")
    res = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "x"}, correlation_id="c1",
    )
    assert res.error_code == "pending_approval"
    approval = approvals.get("c1")
    assert approval.route.require_security is True


# ----- 6: pending → approved → second call proceeds and charges --------------


@pytest.mark.asyncio
async def test_approved_call_proceeds_and_charges(
    connector: Connector, allowlist: Allowlist, approvals: Approvals,
    budget: Any,
) -> None:
    connector.register_service(_build_service("svc1", RiskLevel.MEDIUM))
    allowlist.allow("svc1")
    first = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "x"}, correlation_id="c1",
    )
    assert first.error_code == "pending_approval"

    approvals.decide("c1", decider_id="board:1", decision=Decision.APPROVE)

    second = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "x"}, correlation_id="c1",
    )
    assert second.ok is True
    assert budget.state().spent.amount_usd == Decimal("0.0010")


# ----- 7: denied approval → no charge ---------------------------------------


@pytest.mark.asyncio
async def test_denied_approval_no_charge(
    connector: Connector, allowlist: Allowlist, approvals: Approvals,
    budget: Any,
) -> None:
    connector.register_service(_build_service("svc1", RiskLevel.MEDIUM))
    allowlist.allow("svc1")
    await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "x"}, correlation_id="c1",
    )
    approvals.decide("c1", decider_id="board:1", decision=Decision.DENY)
    res = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "x"}, correlation_id="c1",
    )
    assert res.ok is False
    assert res.error_code == "approval_denied"
    assert budget.state().spent.amount_usd == Decimal("0.0000")


# ----- 8: idempotent correlation returns cached on success -------------------


@pytest.mark.asyncio
async def test_idempotent_correlation_cached(
    connector: Connector, allowlist: Allowlist,
) -> None:
    svc = _build_service("svc1", RiskLevel.LOW)
    connector.register_service(svc)
    allowlist.allow("svc1")
    a = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "x"}, correlation_id="c1",
    )
    b = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "DIFFERENT"}, correlation_id="c1",
    )
    assert a.ok is True and b.ok is True
    # Second call must not have hit the executor again — same cached output.
    assert a.output == b.output


# ----- 9: rate-limit enforced per service -----------------------------------


@pytest.mark.asyncio
async def test_rate_limit_enforced(
    connector: Connector, allowlist: Allowlist,
) -> None:
    svc = _build_service(
        "svc1", RiskLevel.LOW,
        rate=RateLimit(capacity=1, refill_per_second=0.0),
    )
    connector.register_service(svc)
    allowlist.allow("svc1")
    ok = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={}, correlation_id="c1",
    )
    assert ok.ok is True
    blocked = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={}, correlation_id="c2",
    )
    assert blocked.error_code == "rate_limited"


# ----- 10: retry on transient network error ---------------------------------


@pytest.mark.asyncio
async def test_retry_on_network_error(
    connector: Connector, allowlist: Allowlist,
) -> None:
    svc = _build_service("svc1", RiskLevel.LOW, fail_first=2)
    connector.register_service(svc)
    allowlist.allow("svc1")
    res = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "ok"}, correlation_id="c1",
    )
    assert res.ok is True


@pytest.mark.asyncio
async def test_retry_exhausted(
    connector: Connector, allowlist: Allowlist,
) -> None:
    svc = _build_service("svc1", RiskLevel.LOW, fail=True)
    connector.register_service(svc)
    allowlist.allow("svc1")
    res = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={}, correlation_id="c1",
    )
    assert res.ok is False
    assert res.error_code == "external_failure"


# ----- 11: event emitted with cost ------------------------------------------


@pytest.mark.asyncio
async def test_event_emitted(
    connector: Connector, allowlist: Allowlist, event_store: EventStore,
) -> None:
    connector.register_service(_build_service("svc1", RiskLevel.LOW))
    allowlist.allow("svc1")
    await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"text": "x"}, correlation_id="c1",
    )
    rows = event_store.read(kinds=[EventKind.EXTERNAL_CALL])
    assert any(
        e.payload["request_id"] == "c1" and e.payload["status"] == "ok"
        for e in rows
    )


# ----- 12: args validation failure ------------------------------------------


@pytest.mark.asyncio
async def test_args_validation_failure(
    connector: Connector, allowlist: Allowlist,
) -> None:
    connector.register_service(_build_service("svc1", RiskLevel.LOW))
    allowlist.allow("svc1")
    res = await connector.call(
        agent_id="a1", service="svc1", action="call",
        args={"unknown_field": 1}, correlation_id="c1",
    )
    assert res.ok is False
    assert res.error_code == "connector_error"


# ----- 13: HTTP service wiring ----------------------------------------------


@pytest.mark.asyncio
async def test_http_service_call(
    connector: Connector, allowlist: Allowlist, approvals: Approvals,
    http_client: Any,
) -> None:
    connector.register_service(build_http_service(http_client))
    allowlist.allow("http")
    first = await connector.call(
        agent_id="a1", service="http", action="get",
        args={"url": "https://example.com"}, correlation_id="c1",
    )
    assert first.error_code == "pending_approval"
    approvals.decide("c1", decider_id="board:1", decision=Decision.APPROVE)
    second = await connector.call(
        agent_id="a1", service="http", action="get",
        args={"url": "https://example.com"}, correlation_id="c1",
    )
    assert second.ok is True
    out = second.output
    assert out is not None
    assert out.status == 200  # type: ignore[attr-defined]


# ----- 14: email service wiring ---------------------------------------------


@pytest.mark.asyncio
async def test_email_service_call(
    connector: Connector, allowlist: Allowlist, approvals: Approvals,
    email_client: Any,
) -> None:
    connector.register_service(
        build_email_service(email_client, sender="ceo@acme.example")
    )
    allowlist.allow("email", "send")
    first = await connector.call(
        agent_id="a1", service="email", action="send",
        args={
            "to": ["a@example.com"], "subject": "hi", "body": "hello",
        },
        correlation_id="c1",
    )
    assert first.error_code == "pending_approval"
    approvals.decide("c1", decider_id="board:1", decision=Decision.APPROVE)
    second = await connector.call(
        agent_id="a1", service="email", action="send",
        args={
            "to": ["a@example.com"], "subject": "hi", "body": "hello",
        },
        correlation_id="c1",
    )
    assert second.ok is True
    assert email_client.sent[0]["sender"] == "ceo@acme.example"
