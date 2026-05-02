"""Send/route tests covering routing, validation, pause and idempotency."""
from __future__ import annotations

import pytest

from modules.event_store import EventKind, EventStore
from modules.identity import Org
from modules.orchestrator import (
    InMemoryMessageQueue,
    MessageKind,
    Orchestrator,
    SendResult,
)


@pytest.mark.asyncio
async def test_send_queued_when_running(
    orch: Orchestrator, bootstrap_org: dict[str, str],
    queue: InMemoryMessageQueue,
) -> None:
    res = await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="status?",
    )
    assert res is SendResult.QUEUED
    assert queue.pending_count(bootstrap_org["eng"]) == 1


@pytest.mark.asyncio
async def test_send_rejected_when_paused(
    orch: Orchestrator, bootstrap_org: dict[str, str], clock: object,
) -> None:
    clock.pause_sync()  # type: ignore[attr-defined]
    res = await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="hi",
    )
    assert res is SendResult.REJECTED_PAUSED


@pytest.mark.asyncio
async def test_send_rejected_invalid_target(
    orch: Orchestrator, bootstrap_org: dict[str, str],
) -> None:
    res = await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent="agent-ghost",
        content="hi",
    )
    assert res is SendResult.REJECTED_INVALID_TARGET


@pytest.mark.asyncio
async def test_send_rejected_when_recipient_fired(
    orch: Orchestrator, bootstrap_org: dict[str, str], org: Org,
) -> None:
    from modules.identity import Role
    from modules.orchestrator.tests.conftest import FakeAgent

    # Hire a junior under engineer; CEO has depth=2 over junior, so CEO
    # firing through HR results in DIRECT_FIRED.
    junior = org.add_agent(
        role=Role.MEMBER, persona_ref="jr.v1",
        reports_to=bootstrap_org["eng"], requested_by=bootstrap_org["hr"],
        via_hr=True,
    )
    orch.register(FakeAgent(junior.id))

    outcome = org.fire(
        actor_id=bootstrap_org["ceo"], target_id=junior.id,
        reason="restructure", via_hr=True,
    )
    assert outcome.result.value == "direct_fired"
    res = await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=junior.id, content="hi",
    )
    assert res is SendResult.REJECTED_INVALID_TARGET


@pytest.mark.asyncio
async def test_duplicate_correlation_id_dropped(
    orch: Orchestrator, bootstrap_org: dict[str, str],
) -> None:
    res1 = await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="x", correlation_id="c1",
    )
    res2 = await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="x", correlation_id="c1",
    )
    assert res1 is SendResult.QUEUED
    assert res2 is SendResult.REJECTED_DUPLICATE


@pytest.mark.asyncio
async def test_message_too_large_rejected(
    orch: Orchestrator, bootstrap_org: dict[str, str],
) -> None:
    payload = "x" * (256 * 1024 + 1)
    res = await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content=payload,
    )
    assert res is SendResult.REJECTED_TOO_LARGE


@pytest.mark.asyncio
async def test_system_send_exempt_from_rate_limit(
    orch: Orchestrator, bootstrap_org: dict[str, str],
) -> None:
    for _ in range(20):
        r = await orch.system_send(
            bootstrap_org["eng"], "ping", kind=MessageKind.NOTIFY,
        )
        assert r is SendResult.QUEUED


@pytest.mark.asyncio
async def test_drain_dispatches_to_agents(
    orch: Orchestrator, bootstrap_org: dict[str, str],
) -> None:
    await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="do work",
    )
    delivered = await orch.drain()
    assert delivered == 1
    eng_handle = orch._agents[bootstrap_org["eng"]]
    assert len(eng_handle.delivered) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_send_emits_message_sent_event(
    orch: Orchestrator, bootstrap_org: dict[str, str],
    event_store: EventStore,
) -> None:
    await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="hi",
    )
    kinds = [e.kind for e in event_store.read(kinds=[EventKind.MESSAGE_SENT])]
    assert EventKind.MESSAGE_SENT in kinds
