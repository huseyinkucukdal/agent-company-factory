"""Tests for `Quota`."""
from __future__ import annotations

import pytest

from modules.storage import (
    AgentQuota,
    CompanyDB,
    CompanyQuota,
    Quota,
    Workspace,
    WorkspaceNotInitialized,
)


def test_company_quota_set_and_read(company_db: CompanyDB) -> None:
    q = Quota(company_db)
    q.set_limit(CompanyQuota(), mb=5)
    assert q.limit(CompanyQuota()) == 5 * 1024 * 1024
    assert q.usage(CompanyQuota()) == 0
    assert q.remaining(CompanyQuota()) == 5 * 1024 * 1024


def test_agent_quota_set_requires_existing_row(company_db: CompanyDB) -> None:
    q = Quota(company_db)
    with pytest.raises(WorkspaceNotInitialized):
        q.set_limit(AgentQuota("ghost"), mb=1)


def test_agent_quota_after_create(workspace: Workspace, company_db: CompanyDB) -> None:
    workspace.create_for_agent("bob", quota_mb=2)
    q = Quota(company_db)
    assert q.limit(AgentQuota("bob")) == 2 * 1024 * 1024
    assert q.usage(AgentQuota("bob")) == 0


def test_set_limit_rejects_negative(company_db: CompanyDB) -> None:
    q = Quota(company_db)
    with pytest.raises(ValueError):
        q.set_limit(CompanyQuota(), mb=-1)


def test_unknown_scope_type_raises(company_db: CompanyDB) -> None:
    q = Quota(company_db)
    with pytest.raises(TypeError):
        q.usage("not-a-scope")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        q.limit("not-a-scope")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        q.set_limit("not-a-scope", mb=1)  # type: ignore[arg-type]
