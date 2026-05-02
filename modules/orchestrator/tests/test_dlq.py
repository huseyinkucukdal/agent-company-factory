"""DLQ + crash-recovery tests."""
from __future__ import annotations

import pytest

from modules.orchestrator import (
    InMemoryMessageQueue,
    Orchestrator,
    RedisUnavailable,
)


@pytest.mark.asyncio
async def test_dlq_after_repeated_delivery_failures(
    orch: Orchestrator, bootstrap_org: dict[str, str],
) -> None:
    eng_handle = orch._agents[bootstrap_org["eng"]]
    eng_handle.fail_n = 99  # type: ignore[attr-defined]
    await orch.send(
        from_agent=bootstrap_org["ceo"], to_agent=bootstrap_org["eng"],
        content="hi",
    )
    # drain has a max_iters cap so the failing message will be re-queued
    # twice and then DLQ'd.
    await orch.drain(max_iters=10)
    dlq = orch.dlq()
    assert len(dlq) == 1
    assert dlq[0].envelope.to_agent == bootstrap_org["eng"]


@pytest.mark.asyncio
async def test_queue_unavailable_raises_circuit_breaker(
    orch: Orchestrator, bootstrap_org: dict[str, str],
    queue: InMemoryMessageQueue,
) -> None:
    queue.set_available(False)
    with pytest.raises(RedisUnavailable):
        await orch.send(
            from_agent=bootstrap_org["ceo"],
            to_agent=bootstrap_org["eng"], content="hi",
        )
