"""Shared fixtures + fakes for Clock tests."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from modules.clock import Clock, ClockRate
from modules.event_store import EventStore
from modules.storage import CompanyDB

EPOCH = datetime(2030, 1, 1, tzinfo=UTC)


@dataclass
class FakeWallClock:
    """Manually advanceable wall-clock for deterministic tests."""

    now: datetime = EPOCH

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: float = 0, days: float = 0) -> None:
        self.now += timedelta(seconds=seconds, days=days)


@dataclass
class FakeIdleProbe:
    idle: bool = True
    calls: int = 0
    sequence: list[bool] = field(default_factory=list)

    def is_company_idle(self) -> bool:
        self.calls += 1
        if self.sequence:
            return self.sequence.pop(0)
        return self.idle


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def company_db(storage_root: Path) -> Iterator[CompanyDB]:
    db = CompanyDB.init("acme", storage_root)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def event_store(company_db: CompanyDB) -> EventStore:
    es = EventStore(company_db)
    es.migrate()
    return es


@pytest.fixture
def wall() -> FakeWallClock:
    return FakeWallClock()


@pytest.fixture
def idle_probe() -> FakeIdleProbe:
    return FakeIdleProbe()


ClockFactory = Callable[..., Clock]


@pytest.fixture
def clock_factory(
    company_db: CompanyDB,
    event_store: EventStore,
    wall: FakeWallClock,
    idle_probe: FakeIdleProbe,
) -> ClockFactory:
    def _make(rate: ClockRate | None = None) -> Clock:
        c = Clock(
            company_db,
            event_store,
            idle_probe,
            rate=rate or ClockRate.realtime(),
            wall_now=wall,
        )
        c.migrate()
        return c

    return _make


@pytest.fixture
def clock(clock_factory: ClockFactory) -> Clock:
    return clock_factory()
