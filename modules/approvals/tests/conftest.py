"""Shared fixtures for the Approval-System tests."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from modules.approvals import Approvals
from modules.event_store import EventStore
from modules.storage import CompanyDB


class FakeClock:
    """A user-controlled wall-clock used by the timeout tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self.t = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.t

    def advance(self, **kwargs: float) -> None:
        self.t = self.t + timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


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
def approvals(
    company_db: CompanyDB, event_store: EventStore, clock: FakeClock
) -> Approvals:
    svc = Approvals(company_db, event_store, time_provider=clock.now)
    svc.migrate()
    return svc
