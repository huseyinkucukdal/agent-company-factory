"""Embedder behaviour."""
from __future__ import annotations

import math

from modules.memory import FakeEmbedder, HashEmbedder


def _l2(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def test_hash_embedder_is_deterministic() -> None:
    e = HashEmbedder(dim=64)
    assert e.embed("hello world") == e.embed("hello world")


def test_hash_embedder_normalised() -> None:
    e = HashEmbedder(dim=32)
    v = e.embed("the quick brown fox")
    assert abs(_l2(v) - 1.0) < 1e-6


def test_hash_embedder_empty_returns_zero_vector() -> None:
    e = HashEmbedder(dim=8)
    assert e.embed("") == [0.0] * 8


def test_fake_embedder_uses_pinned_then_falls_back() -> None:
    e = FakeEmbedder(dim=4, mapping={"hi": [1.0, 0.0, 0.0, 0.0]})
    assert e.embed("hi") == [1.0, 0.0, 0.0, 0.0]
    # Unknown text falls back to hash-based embedding (length matches dim).
    other = e.embed("something else")
    assert len(other) == 4


def test_fake_embedder_rejects_wrong_dim_in_put() -> None:
    e = FakeEmbedder(dim=4)
    import pytest

    with pytest.raises(ValueError):
        e.put("x", [1.0, 0.0])
