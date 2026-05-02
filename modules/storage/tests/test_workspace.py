"""Tests for `Workspace` write/read/list/delete and quota interactions."""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from modules.storage import (
    AgentQuota,
    CompanyDB,
    CompanyQuota,
    PermissionDenied,
    Quota,
    QuotaExceeded,
    Workspace,
    WorkspaceNotInitialized,
)

from .fakes import FakeEventSink, FakeIdentity

AgentFactory = Callable[..., str]


def test_workspace_write_under_quota_succeeds(
    workspace: Workspace, agent: str, company_db: CompanyDB
) -> None:
    result = workspace.write(agent, "notes/hello.txt", b"hello")
    assert result.bytes_written == 5
    q = Quota(company_db)
    assert q.usage(AgentQuota(agent)) == 5
    assert q.usage(CompanyQuota()) == 5


def test_workspace_write_exceeds_agent_quota_raises(
    workspace: Workspace, agent: str, events: FakeEventSink
) -> None:
    payload = b"x" * (1024 * 1024 + 1)  # 1 MB + 1 byte > 1 MB agent quota
    with pytest.raises(QuotaExceeded):
        workspace.write(agent, "big.bin", payload)
    assert "quota_exceeded" in events.kinds()


def test_company_quota_blocks_when_company_full(
    company_db: CompanyDB,
    identity: FakeIdentity,
    events: FakeEventSink,
) -> None:
    q = Quota(company_db)
    q.set_limit(CompanyQuota(), mb=1)  # 1 MB total
    ws = Workspace(company_db, identity, events)
    ws.create_for_agent("a", quota_mb=2)  # agent allowed more, but company caps
    # Fill 600 KB
    ws.write("a", "f1", b"x" * (600 * 1024))
    # 500 KB more would push company over 1 MB
    with pytest.raises(QuotaExceeded):
        ws.write("a", "f2", b"y" * (500 * 1024))


def test_overwrite_does_not_double_count(
    workspace: Workspace, agent: str, company_db: CompanyDB
) -> None:
    workspace.write(agent, "f.txt", b"a" * 100)
    workspace.write(agent, "f.txt", b"b" * 50)
    q = Quota(company_db)
    assert q.usage(AgentQuota(agent)) == 50


def test_quota_warning_emitted_when_crossing_90_percent(
    workspace: Workspace, agent: str, events: FakeEventSink
) -> None:
    # 1 MB agent quota → 90% = ~943718 bytes
    workspace.write(agent, "big.bin", b"x" * (950 * 1024))
    assert "quota_warning" in events.kinds()


def test_read_own_workspace_succeeds(workspace: Workspace, agent: str) -> None:
    workspace.write(agent, "a/b.txt", b"payload")
    assert workspace.read_own(agent, "a/b.txt") == b"payload"


def test_read_subordinate_when_manager_succeeds(
    workspace: Workspace, agent_factory: AgentFactory, identity: FakeIdentity
) -> None:
    agent_factory("boss")
    agent_factory("worker")
    workspace.write("worker", "report.md", b"done")
    identity.manages[("boss", "worker")] = True
    assert workspace.read_subordinate("boss", "worker", "report.md") == b"done"


def test_read_subordinate_when_not_manager_denied(
    workspace: Workspace, agent_factory: AgentFactory
) -> None:
    agent_factory("nosy")
    agent_factory("victim")
    workspace.write("victim", "secret", b"42")
    with pytest.raises(PermissionDenied):
        workspace.read_subordinate("nosy", "victim", "secret")


def test_read_own_missing_file_raises(workspace: Workspace, agent: str) -> None:
    with pytest.raises(FileNotFoundError):
        workspace.read_own(agent, "no/such.txt")


def test_list_workspace_root(workspace: Workspace, agent: str) -> None:
    workspace.write(agent, "a.txt", b"1")
    workspace.write(agent, "sub/b.txt", b"22")
    metas = workspace.list(agent)
    rels = sorted(m.relative_path for m in metas)
    assert rels == ["a.txt", "sub"]
    a_meta = next(m for m in metas if m.relative_path == "a.txt")
    assert a_meta.size == 1
    assert a_meta.is_dir is False


def test_delete_file_updates_quota(
    workspace: Workspace, agent: str, company_db: CompanyDB
) -> None:
    workspace.write(agent, "f.txt", b"abcdef")
    workspace.delete(agent, "f.txt")
    q = Quota(company_db)
    assert q.usage(AgentQuota(agent)) == 0


def test_delete_directory_refused(workspace: Workspace, agent: str) -> None:
    workspace.write(agent, "d/x", b"1")
    with pytest.raises(IsADirectoryError):
        workspace.delete(agent, "d")


def test_delete_for_agent_removes_files_and_row(
    workspace: Workspace, agent: str, storage_root: Path, company_db: CompanyDB
) -> None:
    workspace.write(agent, "x", b"1")
    workspace.delete_for_agent(agent)
    conn = company_db.connect()
    row = conn.execute(
        "SELECT 1 FROM workspaces WHERE agent_id = ?", (agent,)
    ).fetchone()
    assert row is None
    agent_dir = storage_root / "companies" / "acme" / "workspaces" / agent
    assert not agent_dir.exists()


def test_write_to_uninitialised_agent_raises(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceNotInitialized):
        workspace.write("ghost", "x", b"1")


def test_concurrent_writes_quota_consistent(
    workspace: Workspace, agent_factory: AgentFactory, company_db: CompanyDB
) -> None:
    agent_factory("racer", quota_mb=2)
    payload = b"x" * 1000
    threads = []

    def writer(idx: int) -> None:
        workspace.write("racer", f"f{idx}.bin", payload)

    for i in range(20):
        t = threading.Thread(target=writer, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    q = Quota(company_db)
    assert q.usage(AgentQuota("racer")) == 20 * 1000


def test_reconcile_quota_detects_drift(
    workspace: Workspace, agent: str, events: FakeEventSink, storage_root: Path
) -> None:
    workspace.write(agent, "f.txt", b"1234567890")
    # Tamper with FS behind the cache: append more bytes directly.
    fs_path = storage_root / "companies" / "acme" / "workspaces" / agent / "f.txt"
    fs_path.write_bytes(b"x" * 50)
    drift = workspace.reconcile_quota()
    assert drift.get(agent) == 40  # 50 - 10
    assert "quota_drift" in events.kinds()


def test_archive_company_moves_directory(
    workspace: Workspace, agent: str, storage_root: Path, tmp_path: Path
) -> None:
    workspace.write(agent, "f.txt", b"keep me")
    # Close DB before moving the dir so SQLite doesn't keep the file open.
    workspace._db.close()
    dest = tmp_path / "archive" / "acme"
    workspace.archive_company(dest)
    assert dest.exists()
    assert not (storage_root / "companies" / "acme").exists()
    assert (dest / "workspaces" / agent / "f.txt").read_bytes() == b"keep me"


def test_create_for_agent_rejects_invalid_id(workspace: Workspace) -> None:
    for bad in ["", "..", "a/b", "x\\y"]:
        with pytest.raises(ValueError):
            workspace.create_for_agent(bad, quota_mb=1)
