"""Approval round-trip for EXPENSE / EXTERNAL gated tools."""
from __future__ import annotations

import pytest

from modules.approvals import Approvals, Decision
from modules.cost import Budget
from modules.identity import Agent
from modules.tools import Tools


@pytest.mark.asyncio
async def test_external_call_first_pending_then_approved(
    tools: Tools,
    agents: dict[str, Agent],
    approvals: Approvals,
    budget: Budget,
) -> None:
    cid = "cid-ext-1"

    # First call: approval is PENDING → tool returns pending_approval.
    first = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="external_call",
        args={
            "service": "stripe",
            "endpoint": "/charge",
            "payload": {"amount": 10},
            "estimated_cost_usd": "1.00",
        },
        correlation_id=cid,
    )
    assert not first.ok
    assert first.error_code == "pending_approval"
    request_id = first.extra["approval_request_id"]
    assert request_id == cid

    # Reservation released so budget is fully available again.
    state = budget.state()
    assert state.reserved.amount_usd == 0

    # Decide approve and re-invoke: now the tool runs.
    approvals.decide(request_id, "BOARD", Decision.APPROVE)
    second = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="external_call",
        args={
            "service": "stripe",
            "endpoint": "/charge",
            "payload": {"amount": 10},
            "estimated_cost_usd": "1.00",
        },
        correlation_id=cid,
    )
    assert second.ok
    assert second.output is not None
    assert second.cost_usd > 0


@pytest.mark.asyncio
async def test_external_call_denied_after_pending(
    tools: Tools, agents: dict[str, Agent], approvals: Approvals
) -> None:
    cid = "cid-ext-deny"

    pending = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="external_call",
        args={
            "service": "stripe",
            "endpoint": "/charge",
            "payload": {},
            "estimated_cost_usd": "1.00",
        },
        correlation_id=cid,
    )
    assert pending.error_code == "pending_approval"

    approvals.decide(cid, "BOARD", Decision.DENY)

    denied = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="external_call",
        args={
            "service": "stripe",
            "endpoint": "/charge",
            "payload": {},
            "estimated_cost_usd": "1.00",
        },
        correlation_id=cid,
    )
    assert not denied.ok
    assert denied.error_code == "approval_denied"


@pytest.mark.asyncio
async def test_pay_invoice_gated_by_expense_approval(
    tools: Tools, agents: dict[str, Agent], approvals: Approvals
) -> None:
    cid = "cid-pay"

    pending = await tools.invoke(
        agent_id=agents["cfo"].id,
        tool_name="pay_invoice",
        args={
            "vendor": "Acme Cloud",
            "amount_usd": "12.34",
            "description": "month",
        },
        correlation_id=cid,
    )
    assert pending.error_code == "pending_approval"

    approvals.decide(cid, "BOARD", Decision.APPROVE)
    paid = await tools.invoke(
        agent_id=agents["cfo"].id,
        tool_name="pay_invoice",
        args={
            "vendor": "Acme Cloud",
            "amount_usd": "12.34",
            "description": "month",
        },
        correlation_id=cid,
    )
    assert paid.ok
    assert paid.output is not None
    assert paid.output.model_dump()["paid_usd"] == "12.34"


@pytest.mark.asyncio
async def test_hr_cannot_pay_invoice(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    res = await tools.invoke(
        agent_id=agents["hr"].id,
        tool_name="pay_invoice",
        args={"vendor": "x", "amount_usd": "1.00"},
        correlation_id="cid-hr-pay",
    )
    assert res.error_code == "role_not_allowed"
