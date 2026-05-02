"""Episodic + semantic store basics."""
from __future__ import annotations

from modules.memory import Memory


def test_remember_episode_persists(memory: Memory) -> None:
    eid = memory.remember_episode("a", "shipped feature X", {"task": "T-1"})
    # Recall the episode by similar wording.
    hits = memory.recall("a", "shipped feature X")
    assert any(h.item_id == eid for h in hits)


def test_remember_fact_upsert(memory: Memory) -> None:
    memory.remember_fact("a", "ceo", "Ahmet")
    assert memory.get_fact("a", "ceo") == "Ahmet"
    memory.remember_fact("a", "ceo", "Mehmet")
    assert memory.get_fact("a", "ceo") == "Mehmet"
    facts = memory.all_facts("a")
    assert facts == {"ceo": "Mehmet"}


def test_get_unknown_fact_returns_none(memory: Memory) -> None:
    assert memory.get_fact("a", "nope") is None


def test_facts_isolated_per_agent(memory: Memory) -> None:
    memory.remember_fact("a", "ceo", "Ahmet")
    memory.remember_fact("b", "ceo", "Mehmet")
    assert memory.get_fact("a", "ceo") == "Ahmet"
    assert memory.get_fact("b", "ceo") == "Mehmet"


def test_remember_fact_empty_key_rejected(memory: Memory) -> None:
    import pytest

    with pytest.raises(ValueError):
        memory.remember_fact("a", "", "x")
