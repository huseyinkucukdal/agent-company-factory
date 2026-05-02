"""Shared pytest fixtures for the Storage module tests."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from modules.storage import (
    AgentQuota,
    BoardDB,
    CompanyDB,
    CompanyQuota,
    Quota,
    Workspace,
)

from .fakes import FakeEventSink, FakeIdentity

COMPANY_ID = "acme"
DEFAULT_COMPANY_QUOTA_MB = 10
DEFAULT_AGENT_QUOTA_MB = 1


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def board_db(storage_root: Path) -> Iterator[BoardDB]:
    db = BoardDB.init(storage_root)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def company_db(storage_root: Path) -> Iterator[CompanyDB]:
    db = CompanyDB.init(COMPANY_ID, storage_root)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def quota(company_db: CompanyDB) -> Quota:
    q = Quota(company_db)
    q.set_limit(CompanyQuota(), mb=DEFAULT_COMPANY_QUOTA_MB)
    return q


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture
def events() -> FakeEventSink:
    return FakeEventSink()


@pytest.fixture
def workspace(
    company_db: CompanyDB,
    quota: Quota,  # ensures a non-zero company quota
    identity: FakeIdentity,
    events: FakeEventSink,
) -> Workspace:
    del quota
    return Workspace(company_db, identity, events)


@pytest.fixture
def agent(workspace: Workspace) -> str:
    workspace.create_for_agent("alice", quota_mb=DEFAULT_AGENT_QUOTA_MB)
    return "alice"


@pytest.fixture
def agent_factory(workspace: Workspace) -> Callable[..., str]:
    def _make(agent_id: str, quota_mb: int = DEFAULT_AGENT_QUOTA_MB) -> str:
        workspace.create_for_agent(agent_id, quota_mb=quota_mb)
        return agent_id

    return _make


@pytest.fixture
def agent_quota_scope(agent: str) -> AgentQuota:
    return AgentQuota(agent_id=agent)
