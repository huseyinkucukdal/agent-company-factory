"""Rate limiting tests."""
from __future__ import annotations

import time

import pytest

from modules.orchestrator import (
    Orchestrator,
    RateLimitConfig,
    RateLimiter,
    SendResult,
)


def test_token_bucket_basic() -> None:
    rl = RateLimiter(
        monotonic=time.monotonic,
        config=RateLimitConfig(capacity=2, refill_per_second=0.0),
    )
    assert rl.try_consume("a")
    assert rl.try_consume("a")
    assert not rl.try_consume("a")


def test_token_bucket_refills() -> None:
    fake = [0.0]

    def m() -> float:
        return fake[0]

    rl = RateLimiter(
        monotonic=m,
        config=RateLimitConfig(capacity=1, refill_per_second=10.0),
    )
    assert rl.try_consume("a")
    assert not rl.try_consume("a")
    fake[0] += 1.0
    assert rl.try_consume("a")


@pytest.mark.asyncio
async def test_orchestrator_rate_limit_kicks_in(
    orch: Orchestrator, bootstrap_org: dict[str, str],
) -> None:
    # capacity=3, refill 0 → fourth send fails. Vary content so the
    # loop detector does not preempt the rate limiter.
    for i in range(3):
        r = await orch.send(
            from_agent=bootstrap_org["ceo"],
            to_agent=bootstrap_org["eng"], content=f"task {i}",
        )
        assert r is SendResult.QUEUED
    r = await orch.send(
        from_agent=bootstrap_org["ceo"],
        to_agent=bootstrap_org["eng"], content="task 4",
    )
    assert r is SendResult.REJECTED_RATE_LIMIT
