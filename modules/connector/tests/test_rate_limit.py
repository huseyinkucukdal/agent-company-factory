"""Token-bucket tests."""
from __future__ import annotations

import pytest

from modules.connector.exceptions import RateLimited
from modules.connector.models import RateLimit
from modules.connector.rate_limit import RateLimiter


def test_unconfigured_service_unlimited() -> None:
    rl = RateLimiter()
    for _ in range(100):
        rl.acquire("svc")  # should never raise


def test_capacity_then_block_then_refill() -> None:
    clock = {"t": 0.0}
    rl = RateLimiter(time_provider=lambda: clock["t"])
    rl.configure("svc", RateLimit(capacity=2, refill_per_second=1.0))
    rl.acquire("svc")
    rl.acquire("svc")
    with pytest.raises(RateLimited) as exc:
        rl.acquire("svc")
    assert exc.value.retry_after_seconds > 0
    clock["t"] += 1.5
    rl.acquire("svc")
