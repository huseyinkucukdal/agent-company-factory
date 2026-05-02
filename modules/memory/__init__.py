"""Module 07 — Memory Subsystem.

Three layers per agent:

* **Working** — last-N rolling window of conversation/tool turns.
* **Episodic** — summaries of completed tasks; vector-recallable.
* **Semantic** — typed key→value facts; key lookup or vector-recallable.

Vector search is implemented with Python-side cosine similarity over float32
BLOBs.  The persistence schema reserves a separate ``memory_embeddings`` table
so a future sqlite-vss backend is a drop-in change.
"""
from __future__ import annotations

from .embedders import Embedder, FakeEmbedder, HashEmbedder
from .exceptions import (
    EmbeddingDimensionMismatch,
    MemoryError,
    MemoryPermissionDenied,
    MemoryTooLarge,
)
from .models import Kind, RecallHit, WorkingItem
from .protocols import IdentityProvider
from .service import Memory, migrate

__all__ = [
    "Embedder",
    "EmbeddingDimensionMismatch",
    "FakeEmbedder",
    "HashEmbedder",
    "IdentityProvider",
    "Kind",
    "Memory",
    "MemoryError",
    "MemoryPermissionDenied",
    "MemoryTooLarge",
    "RecallHit",
    "WorkingItem",
    "migrate",
]
