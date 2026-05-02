"""Core invoke pipeline: validation, role gate, cost reservation, events."""
from __future__ import annotations

from decimal import Decimal

import pytest

from modules.cost import Budget, Money
from modules.event_store import EventKind, EventStore
from modules.identity import Agent
from modules.tools import Tools


@pytest.mark.asyncio
async def test_unknown_tool_returns_denied(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    res = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="not_a_tool",
        args={},
        correlation_id="cid-unknown",
    )
    assert not res.ok
    assert res.error_code == "unknown_tool"


@pytest.mark.asyncio
async def test_role_gate_denies_engineer(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    res = await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="report_to_board",
        args={"body": "hi"},
        correlation_id="cid-role",
    )
    assert not res.ok
    assert res.error_code == "role_not_allowed"


@pytest.mark.asyncio
async def test_args_validation_error(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    res = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="send_message",
        args={"recipient_id": "", "body": ""},
        correlation_id="cid-args",
    )
    assert not res.ok
    assert res.error_code == "args_validation_error"


@pytest.mark.asyncio
async def test_send_message_emits_events_and_caches(
    tools: Tools, agents: dict[str, Agent], event_store: EventStore
) -> None:
    res1 = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="send_message",
        args={"recipient_id": agents["eng"].id, "body": "hello"},
        correlation_id="cid-msg",
    )
    assert res1.ok
    assert res1.cost_usd == Decimal("0.0000")

    # Idempotent re-invoke returns the same result object.
    res2 = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="send_message",
        args={"recipient_id": agents["eng"].id, "body": "hello"},
        correlation_id="cid-msg",
    )
    assert res2 is res1

    kinds = [e.kind for e in event_store.read()]
    assert EventKind.TOOL_CALLED in kinds
    assert EventKind.TOOL_RESULT in kinds
    assert EventKind.MESSAGE_SENT in kinds


@pytest.mark.asyncio
async def test_over_budget_blocks_external(
    tools: Tools, agents: dict[str, Agent], budget: Budget
) -> None:
    budget.set_total(Money.of(Decimal("0.50")))
    res = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="external_call",
        args={
            "service": "stripe",
            "endpoint": "/charge",
            "payload": {},
            "estimated_cost_usd": "100.00",
        },
        correlation_id="cid-over",
    )
    assert not res.ok
    assert res.error_code == "over_budget"


@pytest.mark.asyncio
async def test_workspace_round_trip(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    write = await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="write_my_workspace",
        args={"path": "notes.md", "content": "hello world"},
        correlation_id="cid-w",
    )
    assert write.ok

    read = await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="read_my_workspace",
        args={"path": "notes.md"},
        correlation_id="cid-r",
    )
    assert read.ok
    assert read.output is not None
    assert read.output.model_dump()["content"] == "hello world"


@pytest.mark.asyncio
async def test_subordinate_read_via_identity(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    # Engineer writes; CEO (their manager) can read.
    await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="write_my_workspace",
        args={"path": "report.txt", "content": "draft"},
        correlation_id="cid-sw",
    )
    res = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="read_subordinate_workspace",
        args={"owner_id": agents["eng"].id, "path": "report.txt"},
        correlation_id="cid-sr",
    )
    assert res.ok
    assert res.output is not None
    assert res.output.model_dump()["content"] == "draft"


@pytest.mark.asyncio
async def test_memory_remember_and_recall(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="remember_fact",
        args={"key": "favorite_color", "value": "blue"},
        correlation_id="cid-rf",
    )
    res = await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="recall_memory",
        args={"query": "blue", "k": 3},
        correlation_id="cid-rc",
    )
    assert res.ok
    assert res.output is not None
    hits = res.output.model_dump()["hits"]
    assert any("blue" in h["content"] for h in hits)
