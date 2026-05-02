"""Connector adapter — verifies the ServiceDef built by Module 16."""
from __future__ import annotations

import asyncio
from typing import Any

from modules.connector.models import ServiceDef
from modules.inter_company import build_inter_company_service
from modules.inter_company.connector_adapter import (
    InterCompanySendArgs,
    InterCompanySendResult,
)
from modules.inter_company.models import CrossMessageKind, CrossStatus


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_build_service_shape(service: Any) -> None:
    svc = build_inter_company_service(
        from_company="co_a", inter_company=service,
    )
    assert isinstance(svc, ServiceDef)
    assert svc.name == "inter_company"
    assert "send" in svc.actions
    send = svc.actions["send"]
    assert send.args_schema is InterCompanySendArgs
    assert send.output_schema is InterCompanySendResult
    assert send.idempotent is True
    assert send.rate_limit is not None


def test_executor_delivers_via_inter_company(
    service: Any, approved_link, factory: Any,
) -> None:
    svc = build_inter_company_service(
        from_company="co_a", inter_company=service,
    )
    args = InterCompanySendArgs(
        link_id=approved_link.id,
        from_agent="agent_a_eng_1",
        kind=CrossMessageKind.INQUIRY,
        subject="hi",
        body="please respond",
    )
    out = _run(svc.actions["send"].executor(args))
    assert isinstance(out, InterCompanySendResult)
    assert out.status is CrossStatus.DELIVERED
    assert out.target_agent == "b_hr"
    assert factory.handles["co_b"].orchestrator.delivered


def test_executor_returns_rejected_on_violation(
    service: Any, approved_link,
) -> None:
    svc = build_inter_company_service(
        from_company="co_a", inter_company=service,
    )
    # Wrong direction — link is A->B, claim from co_b is also wrong, but
    # the closure pins from_company=co_a. So forge a non-existent link
    # to trigger LinkNotFound surfaced as REJECTED.
    args = InterCompanySendArgs(
        link_id="missing-link-xyz",
        from_agent="x",
        kind=CrossMessageKind.GENERIC,
        subject="s",
        body="b",
    )
    out = _run(svc.actions["send"].executor(args))
    assert out.status is CrossStatus.REJECTED
    assert out.reason and "LinkNotFound" in out.reason


def test_cost_and_sanitizer_are_safe(service: Any, approved_link) -> None:
    svc = build_inter_company_service(
        from_company="co_a", inter_company=service,
    )
    args = InterCompanySendArgs(
        link_id=approved_link.id,
        from_agent="x",
        kind=CrossMessageKind.INQUIRY,
        subject="s", body="b",
    )
    cost = svc.actions["send"].cost_estimator(args)
    assert int(cost.amount_usd * 10000) == 0
    out = _run(svc.actions["send"].executor(args))
    sanitized = svc.actions["send"].sanitizer(out)
    assert isinstance(sanitized, InterCompanySendResult)
    assert sanitized.id == out.id
