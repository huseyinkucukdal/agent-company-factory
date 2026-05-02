"""Shared fixtures for the Event Store tests."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from modules.event_store import EventStore
from modules.storage import CompanyDB


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
def store(company_db: CompanyDB) -> EventStore:
    s = EventStore(company_db)
    s.migrate()
    return s
