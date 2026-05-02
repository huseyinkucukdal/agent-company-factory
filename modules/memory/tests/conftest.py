"""Shared fixtures for memory tests."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from modules.memory import FakeEmbedder, Memory
from modules.memory.protocols import IdentityProvider
from modules.storage import CompanyDB


class StubIdentity:
    """In-memory identity stub.

    ``manager_of`` maps owner→manager; ``fired`` is a set of agents whose
    ``is_active`` returns False.
    """

    def __init__(
        self,
        manager_of: dict[str, str] | None = None,
        fired: set[str] | None = None,
    ) -> None:
        self.manager_of = manager_of or {}
        self.fired = fired or set()

    def is_active(self, agent_id: str) -> bool:
        return agent_id not in self.fired

    def can_read_workspace(self, reader_id: str, owner_id: str) -> bool:
        if reader_id == owner_id:
            return True
        return self.manager_of.get(owner_id) == reader_id


def now() -> datetime:
    return datetime(2026, 4, 30, 12, 0, tzinfo=UTC)


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
def identity() -> StubIdentity:
    return StubIdentity()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder(dim=4)


@pytest.fixture
def memory(
    company_db: CompanyDB,
    identity: IdentityProvider,
    embedder: FakeEmbedder,
) -> Memory:
    m = Memory(company_db, identity, embedder, working_size=3)
    m.migrate()
    return m
