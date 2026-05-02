"""Shared fixtures for cost-engine tests."""
from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from modules.cost import (
    Budget,
    LlmRate,
    Money,
    Pricing,
    Subscriptions,
    migrate,
)
from modules.event_store import EventStore
from modules.storage import CompanyDB


def usd(amount: str | int | float) -> Money:
    return Money.of(Decimal(str(amount)))


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def company_db(storage_root: Path) -> Iterator[CompanyDB]:
    db = CompanyDB.init("acme", storage_root)
    db.migrate()
    migrate(db)
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
def budget(company_db: CompanyDB, event_store: EventStore) -> Budget:
    return Budget(company_db, event_store)


@pytest.fixture
def pricing() -> Pricing:
    rates = {
        "test-model": LlmRate(
            input_per_1k=Decimal("0.0030"),
            output_per_1k=Decimal("0.0150"),
        )
    }
    return Pricing(llm_rates=rates)


@pytest.fixture
def subscriptions(
    company_db: CompanyDB, budget: Budget, event_store: EventStore
) -> Subscriptions:
    return Subscriptions(company_db, budget, event_store)
