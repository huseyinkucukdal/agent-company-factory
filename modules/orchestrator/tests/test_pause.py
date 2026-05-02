"""Pause + idle probe tests."""
from __future__ import annotations

import pytest

from modules.orchestrator import Orchestrator, SendResult


@pytest.mark.asyncio
async def test_idle_probe_true_when_all_idle_queue_empty(
    orch: Orchestrator,
) -> None:
    assert orch.is_company_idle() is True


@pytest.mark.asyncio
async def test_idle_probe_false_when_queue_has_messages(
    orch: Orchestrator, bootstrap_org: dict[str, str],
) -> None:
    await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="hi",
    )
    assert orch.is_company_idle() is False


@pytest.mark.asyncio
async def test_drain_blocked_while_paused(
    orch: Orchestrator, bootstrap_org: dict[str, str], clock: object,
) -> None:
    await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="hi",
    )
    clock.pause_sync()  # type: ignore[attr-defined]
    delivered = await orch.drain(max_iters=3)
    # No agents are dispatched while paused.
    assert delivered == 0
    clock.resume_sync()  # type: ignore[attr-defined]
    delivered = await orch.drain()
    assert delivered == 1


@pytest.mark.asyncio
async def test_send_after_resume(
    orch: Orchestrator, bootstrap_org: dict[str, str], clock: object,
) -> None:
    clock.pause_sync()  # type: ignore[attr-defined]
    res = await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="hi",
    )
    assert res is SendResult.REJECTED_PAUSED
    clock.resume_sync()  # type: ignore[attr-defined]
    res = await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="hi",
    )
    assert res is SendResult.QUEUED
