"""Shared fixtures for the Efficiency-module tests."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from modules.approvals import Approvals
from modules.efficiency import EfficiencyService, FindingStore
from modules.event_store import EventStore
from modules.storage import CompanyDB


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.t = start or datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.t

    def advance(self, **kwargs: float) -> None:
        self.t = self.t + timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def company_db(tmp_path: Path) -> Iterator[CompanyDB]:
    db = CompanyDB.init("acme", tmp_path)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def event_store(company_db: CompanyDB, clock: FakeClock) -> EventStore:
    es = EventStore(company_db, company_now=clock.now)
    es.migrate()
    return es


@pytest.fixture
def approvals(
    company_db: CompanyDB, event_store: EventStore, clock: FakeClock
) -> Approvals:
    svc = Approvals(company_db, event_store, time_provider=clock.now)
    svc.migrate()
    return svc


@pytest.fixture
def finding_store(company_db: CompanyDB, clock: FakeClock) -> FindingStore:
    s = FindingStore(company_db, time_provider=clock.now)
    s.migrate()
    return s


@pytest.fixture
def service(
    company_db: CompanyDB,
    event_store: EventStore,
    finding_store: FindingStore,
    approvals: Approvals,
    clock: FakeClock,
) -> EfficiencyService:
    return EfficiencyService(
        company_id="acme",
        db=company_db,
        events=event_store,
        store=finding_store,
        approvals=approvals,
        time_provider=clock.now,
    )
