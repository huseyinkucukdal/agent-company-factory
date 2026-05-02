"""Memory-subsystem exception hierarchy."""
from __future__ import annotations


class MemoryError(Exception):
    """Base class for memory-subsystem errors."""


class MemoryPermissionDenied(MemoryError):
    """Reader is not allowed to access the target agent's memory."""


class EmbeddingDimensionMismatch(MemoryError):
    """An embedder produced a vector whose dimension differs from the index."""


class MemoryTooLarge(MemoryError):
    """A single memory item exceeds the configured byte limit."""
