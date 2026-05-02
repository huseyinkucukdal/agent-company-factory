"""Cross-agent permission rules."""
from __future__ import annotations

import pytest

from modules.memory import (
    FakeEmbedder,
    Memory,
    MemoryPermissionDenied,
)
from modules.storage import CompanyDB

from .conftest import StubIdentity


def test_recall_other_agent_denied(company_db: CompanyDB) -> None:
    identity = StubIdentity()
    mem = Memory(company_db, identity, FakeEmbedder(dim=4))
    mem.migrate()
    mem.remember_fact("alice", "secret", "shhh")
    with pytest.raises(MemoryPermissionDenied):
        mem.recall("alice", "secret", for_reader="bob")


def test_recall_other_agent_as_manager_allowed(company_db: CompanyDB) -> None:
    # bob manages alice → can read.
    identity = StubIdentity(manager_of={"alice": "bob"})
    emb = FakeEmbedder(
        dim=4,
        mapping={"shipped feature X": [1.0, 0.0, 0.0, 0.0]},
    )
    mem = Memory(company_db, identity, emb)
    mem.migrate()
    eid = mem.remember_episode("alice", "shipped feature X")
    hits = mem.recall("alice", "shipped feature X", for_reader="bob")
    assert any(h.item_id == eid for h in hits)


def test_recall_self_for_reader_ok(company_db: CompanyDB) -> None:
    identity = StubIdentity()
    emb = FakeEmbedder(
        dim=4, mapping={"x": [1.0, 0.0, 0.0, 0.0]}
    )
    mem = Memory(company_db, identity, emb)
    mem.migrate()
    mem.remember_episode("alice", "x")
    hits = mem.recall("alice", "x", for_reader="alice")
    assert hits  # self-read always allowed
