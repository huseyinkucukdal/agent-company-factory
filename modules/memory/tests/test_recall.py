"""Recall ranking, kind filter, threshold, forget, fired-agent exclusion."""
from __future__ import annotations

from modules.memory import (
    EmbeddingDimensionMismatch,
    FakeEmbedder,
    Kind,
    Memory,
)
from modules.memory.protocols import IdentityProvider
from modules.storage import CompanyDB

from .conftest import StubIdentity


def _embedder_with_axes() -> FakeEmbedder:
    """4-D embedder pinning each phrase to a distinct axis (or near-axis)."""
    return FakeEmbedder(
        dim=4,
        mapping={
            "shipped feature X": [1.0, 0.0, 0.0, 0.0],
            "deployed feature X": [0.95, 0.05, 0.0, 0.0],   # close to axis 0
            "team lunch on friday": [0.0, 1.0, 0.0, 0.0],
            "company is fintech": [0.0, 0.0, 1.0, 0.0],
            "ceo is ahmet": [0.0, 0.0, 0.0, 1.0],
            "totally unrelated": [0.0, 0.5, 0.5, 0.5],
        },
    )


def test_recall_returns_relevant(
    company_db: CompanyDB, identity: IdentityProvider
) -> None:
    emb = _embedder_with_axes()
    mem = Memory(company_db, identity, emb)
    mem.migrate()
    e1 = mem.remember_episode("a", "shipped feature X")
    mem.remember_episode("a", "team lunch on friday")
    hits = mem.recall("a", "deployed feature X", k=5)
    assert hits  # got something
    assert hits[0].item_id == e1
    assert hits[0].score > 0.9


def test_recall_filters_by_kind(
    company_db: CompanyDB, identity: IdentityProvider
) -> None:
    emb = _embedder_with_axes()
    mem = Memory(company_db, identity, emb)
    mem.migrate()
    e_ep = mem.remember_episode("a", "shipped feature X")
    s_id = mem.remember_fact("a", "ceo", "ceo is ahmet")
    only_episodic = mem.recall(
        "a", "shipped feature X", kinds=[Kind.EPISODIC]
    )
    assert {h.item_id for h in only_episodic} == {e_ep}
    only_semantic = mem.recall("a", "ceo is ahmet", kinds=[Kind.SEMANTIC])
    assert {h.item_id for h in only_semantic} == {s_id}


def test_recall_threshold_filters_low_score(
    company_db: CompanyDB, identity: IdentityProvider
) -> None:
    emb = _embedder_with_axes()
    mem = Memory(company_db, identity, emb)
    mem.migrate()
    mem.remember_episode("a", "team lunch on friday")
    # Query is on a different axis; cosine ~ 0 < 0.3 threshold.
    hits = mem.recall("a", "shipped feature X")
    assert hits == []


def test_recall_empty_query_returns_empty(memory: Memory) -> None:
    memory.remember_episode("a", "x")
    assert memory.recall("a", "") == []


def test_forget_removes_from_recall(
    company_db: CompanyDB, identity: IdentityProvider
) -> None:
    emb = _embedder_with_axes()
    mem = Memory(company_db, identity, emb)
    mem.migrate()
    e = mem.remember_episode("a", "shipped feature X")
    assert any(h.item_id == e for h in mem.recall("a", "shipped feature X"))
    mem.forget("a", e)
    assert mem.recall("a", "shipped feature X") == []


def test_fired_agent_memory_excluded(
    company_db: CompanyDB,
) -> None:
    identity = StubIdentity(fired={"a"})
    emb = _embedder_with_axes()
    mem = Memory(company_db, identity, emb)
    mem.migrate()
    mem.remember_episode("a", "shipped feature X")
    assert mem.recall("a", "shipped feature X") == []


def test_embedding_dim_mismatch_detected(
    company_db: CompanyDB, identity: IdentityProvider
) -> None:
    emb_small = FakeEmbedder(dim=4)
    mem = Memory(company_db, identity, emb_small)
    mem.migrate()
    mem.remember_episode("a", "hello")

    # Now construct a different Memory pointing at the same DB but with a
    # different embedder dimension — recall must detect the mismatch.
    emb_big = FakeEmbedder(dim=8)
    mem_big = Memory(company_db, identity, emb_big)
    import pytest

    with pytest.raises(EmbeddingDimensionMismatch):
        mem_big.recall("a", "hello")
