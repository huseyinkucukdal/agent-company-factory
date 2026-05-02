"""Per-service token-bucket rate limiter.

Thread-safe, monotonic-clock based. Lazy refill: ``acquire`` tops the bucket
up by ``elapsed * refill_per_second`` (capped at ``capacity``) and consumes
one token if available, otherwise reports ``retry_after_seconds``.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .exceptions import RateLimited
from .models import RateLimit


@dataclass
class _Bucket:
    tokens: float
    last: float


class RateLimiter:
    def __init__(self, time_provider: object | None = None) -> None:
        # ``time_provider`` is ``Callable[[], float]`` returning monotonic
        # seconds. Defaults to :func:`time.monotonic`.
        self._now: object = time_provider or time.monotonic
        self._buckets: dict[str, _Bucket] = {}
        self._configs: dict[str, RateLimit] = {}
        self._lock = threading.Lock()

    def configure(self, service: str, limit: RateLimit) -> None:
        with self._lock:
            self._configs[service] = limit
            self._buckets[service] = _Bucket(
                tokens=float(limit.capacity), last=self._now()  # type: ignore[operator]
            )

    def acquire(self, service: str) -> None:
        """Consume one token; raise :class:`RateLimited` if empty."""
        with self._lock:
            limit = self._configs.get(service)
            if limit is None:
                return  # unconfigured services are unlimited
            bucket = self._buckets[service]
            now = float(self._now())  # type: ignore[operator]
            elapsed = max(0.0, now - bucket.last)
            bucket.tokens = min(
                float(limit.capacity),
                bucket.tokens + elapsed * limit.refill_per_second,
            )
            bucket.last = now
            if bucket.tokens < 1.0:
                missing = 1.0 - bucket.tokens
                retry = missing / max(limit.refill_per_second, 1e-9)
                raise RateLimited(retry)
            bucket.tokens -= 1.0
