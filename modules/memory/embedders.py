"""Embedder implementations.

Two are provided:

* :class:`HashEmbedder` — dependency-free bag-of-tokens hashing into a fixed
  number of buckets, then L2-normalised. Quality is mediocre but it is
  deterministic, fast, and good enough as a default until a real model
  (``sentence-transformers``) is wired in production.
* :class:`FakeEmbedder` — used by tests; lets the test author pin specific
  vectors to specific texts so recall ordering is deterministic.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


class Embedder(Protocol):
    """Anything that turns text into a fixed-dimensional float vector."""

    @property
    def dim(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class HashEmbedder:
    """Default deterministic embedder. No external dependencies."""

    def __init__(self, dim: int = 128) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for tok in _tokens(text):
            digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            return vec
        return [x / norm for x in vec]


class FakeEmbedder:
    """Test helper. Maps explicit text→vector pairs; falls back to hash."""

    def __init__(
        self,
        dim: int,
        mapping: dict[str, list[float]] | None = None,
    ) -> None:
        self._dim = dim
        self._mapping: dict[str, list[float]] = {}
        for k, v in (mapping or {}).items():
            self.put(k, v)
        self._fallback = HashEmbedder(dim=dim)

    @property
    def dim(self) -> int:
        return self._dim

    def put(self, text: str, vector: list[float]) -> None:
        if len(vector) != self._dim:
            raise ValueError(
                f"vector len {len(vector)} != dim {self._dim}"
            )
        self._mapping[text] = list(vector)

    def embed(self, text: str) -> list[float]:
        if text in self._mapping:
            return list(self._mapping[text])
        return self._fallback.embed(text)
