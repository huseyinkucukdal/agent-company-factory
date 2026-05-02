"""The :class:`Memory` service.

Concurrency: every state-changing method runs inside a single
``CompanyDB.transaction()`` (BEGIN IMMEDIATE) which serialises writers.
"""
from __future__ import annotations

import json
import math
import struct
import uuid
from datetime import UTC, datetime
from typing import Any

from modules.storage import CompanyDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .embedders import Embedder
from .exceptions import (
    EmbeddingDimensionMismatch,
    MemoryPermissionDenied,
    MemoryTooLarge,
)
from .models import Kind, RecallHit, WorkingItem
from .protocols import IdentityProvider

_MODULE_KEY = "memory"

_DEFAULT_WORKING_SIZE = 50
_DEFAULT_MAX_CONTENT_BYTES = 256 * 1024
_DEFAULT_RECALL_THRESHOLD = 0.3


def migrate(db: CompanyDB) -> None:
    """Apply the memory schema. Safe to call multiple times."""
    migrations = load_migrations_from_package("modules.memory.migrations")
    apply_migrations(db.connect(), module=_MODULE_KEY, migrations=migrations)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def _pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_vector(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):  # pragma: no cover — caller filters by dim
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _dump_meta(meta: dict[str, Any] | None) -> str | None:
    if not meta:
        return None
    return json.dumps(meta, separators=(",", ":"), sort_keys=True)


def _load_meta(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    out: dict[str, Any] = json.loads(raw)
    return out


class Memory:
    """Per-company memory service. One instance per :class:`CompanyDB`."""

    def __init__(
        self,
        db: CompanyDB,
        identity: IdentityProvider,
        embedder: Embedder,
        *,
        working_size: int = _DEFAULT_WORKING_SIZE,
        max_content_bytes: int = _DEFAULT_MAX_CONTENT_BYTES,
        recall_threshold: float = _DEFAULT_RECALL_THRESHOLD,
    ) -> None:
        if working_size <= 0:
            raise ValueError("working_size must be positive")
        self._db = db
        self._identity = identity
        self._embedder = embedder
        self._working_size = working_size
        self._max_content_bytes = max_content_bytes
        self._recall_threshold = recall_threshold

    def migrate(self) -> None:
        migrate(self._db)

    # ----------------------------------------------------------- working

    def append_working(
        self, agent_id: str, item: WorkingItem
    ) -> None:
        self._guard_size(item.content)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO memory_working "
                "(agent_id, role, content, metadata_json, ts_company, ts_real) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    item.role,
                    item.content,
                    _dump_meta(item.metadata),
                    item.ts_company.isoformat(),
                    _utcnow().isoformat(),
                ),
            )

    def working_window(
        self, agent_id: str, n: int | None = None
    ) -> list[WorkingItem]:
        limit = n if n is not None else self._working_size
        if limit <= 0:
            return []
        rows = self._db.connect().execute(
            "SELECT role, content, metadata_json, ts_company "
            "FROM memory_working "
            "WHERE agent_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        items = [
            WorkingItem(
                role=r["role"],
                content=r["content"],
                metadata=_load_meta(r["metadata_json"]),
                ts_company=datetime.fromisoformat(r["ts_company"]),
            )
            for r in rows
        ]
        items.reverse()  # caller wants oldest→newest
        return items

    def clear_working(self, agent_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM memory_working WHERE agent_id = ?", (agent_id,)
            )

    # ----------------------------------------------------------- episodic

    def remember_episode(
        self,
        agent_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self._guard_size(summary)
        memory_id = _new_id()
        vec = self._embedder.embed(summary)
        self._guard_dim(vec)
        now = _utcnow().isoformat()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO memories "
                "(id, agent_id, kind, key, content, metadata_json, ts_real) "
                "VALUES (?, ?, ?, NULL, ?, ?, ?)",
                (
                    memory_id,
                    agent_id,
                    Kind.EPISODIC.value,
                    summary,
                    _dump_meta(metadata),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO memory_embeddings "
                "(memory_id, agent_id, dim, vector) VALUES (?, ?, ?, ?)",
                (memory_id, agent_id, len(vec), _pack_vector(vec)),
            )
        return memory_id

    # ----------------------------------------------------------- semantic

    def remember_fact(
        self,
        agent_id: str,
        key: str,
        value: str,
        *,
        embed: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if not key:
            raise ValueError("key must be non-empty")
        self._guard_size(value)
        now = _utcnow().isoformat()
        with self._db.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM memories "
                "WHERE agent_id = ? AND kind = ? AND key = ?",
                (agent_id, Kind.SEMANTIC.value, key),
            ).fetchone()
            if existing is not None:
                memory_id = str(existing["id"])
                conn.execute(
                    "UPDATE memories "
                    "SET content = ?, metadata_json = ?, ts_real = ? "
                    "WHERE id = ?",
                    (value, _dump_meta(metadata), now, memory_id),
                )
                conn.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id = ?",
                    (memory_id,),
                )
            else:
                memory_id = _new_id()
                conn.execute(
                    "INSERT INTO memories "
                    "(id, agent_id, kind, key, content, metadata_json, ts_real) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        memory_id,
                        agent_id,
                        Kind.SEMANTIC.value,
                        key,
                        value,
                        _dump_meta(metadata),
                        now,
                    ),
                )
            if embed:
                vec = self._embedder.embed(value)
                self._guard_dim(vec)
                conn.execute(
                    "INSERT INTO memory_embeddings "
                    "(memory_id, agent_id, dim, vector) VALUES (?, ?, ?, ?)",
                    (memory_id, agent_id, len(vec), _pack_vector(vec)),
                )
        return memory_id

    def get_fact(self, agent_id: str, key: str) -> str | None:
        row = self._db.connect().execute(
            "SELECT content FROM memories "
            "WHERE agent_id = ? AND kind = ? AND key = ?",
            (agent_id, Kind.SEMANTIC.value, key),
        ).fetchone()
        return None if row is None else str(row["content"])

    def all_facts(self, agent_id: str) -> dict[str, str]:
        rows = self._db.connect().execute(
            "SELECT key, content FROM memories "
            "WHERE agent_id = ? AND kind = ? ORDER BY ts_real",
            (agent_id, Kind.SEMANTIC.value),
        ).fetchall()
        return {r["key"]: r["content"] for r in rows if r["key"] is not None}

    # ------------------------------------------------------------- recall

    def recall(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
        *,
        kinds: list[Kind] | None = None,
        for_reader: str | None = None,
    ) -> list[RecallHit]:
        if not query:
            return []
        if for_reader is not None and for_reader != agent_id and not (
            self._identity.can_read_workspace(for_reader, agent_id)
        ):
            raise MemoryPermissionDenied(
                f"{for_reader} cannot read {agent_id}"
            )
        # Fired agents are excluded from recall (rows persist for replay).
        if not self._identity.is_active(agent_id):
            return []

        qvec = self._embedder.embed(query)
        self._guard_dim(qvec)

        kind_values: list[str] = (
            [k.value for k in kinds] if kinds else [k.value for k in Kind]
        )
        placeholders = ",".join("?" * len(kind_values))
        rows = self._db.connect().execute(
            f"SELECT m.id, m.kind, m.content, m.metadata_json, "
            f"       e.dim, e.vector "
            f"FROM memories m "
            f"JOIN memory_embeddings e ON e.memory_id = m.id "
            f"WHERE m.agent_id = ? AND m.kind IN ({placeholders})",
            (agent_id, *kind_values),
        ).fetchall()

        hits: list[RecallHit] = []
        for r in rows:
            dim: int = int(r["dim"])
            if dim != self._embedder.dim:
                raise EmbeddingDimensionMismatch(
                    f"stored dim {dim} != embedder dim {self._embedder.dim}"
                )
            vec = _unpack_vector(bytes(r["vector"]), dim)
            score = _cosine(qvec, vec)
            if score < self._recall_threshold:
                continue
            hits.append(
                RecallHit(
                    kind=Kind(r["kind"]),
                    item_id=str(r["id"]),
                    content=str(r["content"]),
                    score=score,
                    metadata=_load_meta(r["metadata_json"]),
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    # -------------------------------------------------------------- forget

    def forget(self, agent_id: str, item_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM memories WHERE id = ? AND agent_id = ?",
                (item_id, agent_id),
            )
            # FK ON DELETE CASCADE handles memory_embeddings.

    # ------------------------------------------------------------- helpers

    def _guard_size(self, content: str) -> None:
        if len(content.encode("utf-8")) > self._max_content_bytes:
            raise MemoryTooLarge(
                f"content > {self._max_content_bytes} bytes"
            )

    def _guard_dim(self, vec: list[float]) -> None:
        if len(vec) != self._embedder.dim:
            raise EmbeddingDimensionMismatch(
                f"vector dim {len(vec)} != embedder dim {self._embedder.dim}"
            )
