"""Token-bucket rate limiter, per-agent.

Refills proportionally to elapsed wall time. The bucket is consumed on
each successful :meth:`Orchestrator.send` and skipped for ``system_send``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class RateLimitConfig:
    capacity: int = 60          # max tokens (== max burst)
    refill_per_second: float = 1.0  # 60/min default
    system_exempt: bool = True


class _Bucket:
    __slots__ = ("last", "tokens")

    def __init__(self, tokens: float, last: float) -> None:
        self.tokens = tokens
        self.last = last


class RateLimiter:
    """Per-agent token bucket. Wall-clock driven for fairness across pauses."""

    def __init__(
        self, *, monotonic: object, config: RateLimitConfig | None = None
    ) -> None:
        self._monotonic = monotonic
        self._cfg = config or RateLimitConfig()
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return float(self._monotonic())  # type: ignore[operator]

    def configure(self, agent_id: str, *, capacity: int, refill_per_second: float) -> None:
        with self._lock:
            self._buckets[agent_id] = _Bucket(
                tokens=float(capacity), last=self._now()
            )
        self._cfg = RateLimitConfig(
            capacity=capacity, refill_per_second=refill_per_second,
            system_exempt=self._cfg.system_exempt,
        )

    def try_consume(self, agent_id: str, *, cost: float = 1.0) -> bool:
        with self._lock:
            b = self._buckets.get(agent_id)
            now = self._now()
            if b is None:
                b = _Bucket(tokens=float(self._cfg.capacity), last=now)
                self._buckets[agent_id] = b
            elapsed = max(0.0, now - b.last)
            b.tokens = min(
                float(self._cfg.capacity),
                b.tokens + elapsed * self._cfg.refill_per_second,
            )
            b.last = now
            if b.tokens >= cost:
                b.tokens -= cost
                return True
            return False

    def tokens(self, agent_id: str) -> float:
        with self._lock:
            b = self._buckets.get(agent_id)
            return b.tokens if b else float(self._cfg.capacity)


__all__ = ["RateLimitConfig", "RateLimiter"]
