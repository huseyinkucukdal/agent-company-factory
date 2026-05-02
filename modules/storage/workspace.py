"""Per-agent workspace filesystem.

Responsibilities:
    * Create/delete per-agent workspace directories under
      ``<root>/companies/<company_id>/workspaces/<agent_id>``.
    * Resolve ``(agent_id, relative_path)`` into an absolute path that is
      provably inside that base, defeating ``..``, absolute-path,
      and symlink-escape attacks.
    * Enforce two-level disk quota (per-agent + per-company) atomically with
      a SQLite ``IMMEDIATE`` transaction holding a row-lock on
      ``workspaces.used_bytes``.
    * Permission-check cross-agent reads via :class:`IdentityProvider`.
    * Emit notable storage events (``quota_warning``, ``quota_drift``,
      ``quota_exceeded``) on the injected :class:`EventSink`.

The class never trusts a caller-supplied path. Agents pass
``(agent_id, relative_path)``; the class is the *only* component that turns
that into an absolute filesystem path.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from .db import CompanyDB
from .exceptions import (
    PathOutsideWorkspace,
    PermissionDenied,
    QuotaExceeded,
    WorkspaceNotInitialized,
)
from .protocols import EventSink, IdentityProvider

_WARNING_RATIO: Final[float] = 0.9
PROJECT_WORKSPACE_ID: Final[str] = "__project__"


@dataclass(frozen=True)
class WriteResult:
    """Result of a successful :meth:`Workspace.write`.

    Remaining values are reported in megabytes for ergonomic logging; callers
    that need exact bytes can use :class:`Quota`.
    """

    bytes_written: int
    company_remaining_mb: float
    agent_remaining_mb: float


@dataclass(frozen=True)
class FileMeta:
    """Listing entry inside a workspace."""

    relative_path: str
    size: int
    is_dir: bool
    modified_at: float  # POSIX timestamp


class Workspace:
    """Per-company filesystem facade for agent workspaces."""

    def __init__(
        self,
        db: CompanyDB,
        identity: IdentityProvider,
        events: EventSink,
    ) -> None:
        self._db = db
        self._identity = identity
        self._events = events
        # Serialise FS-mutating calls within one process. SQLite transactions
        # already serialise across processes; this lock prevents two threads
        # in the same process from racing on the same file.
        self._fs_lock = threading.Lock()

    # ------------------------------------------------------------------ paths

    @property
    def _company_root(self) -> Path:
        return self._db.root / "companies" / self._db.company_id

    @property
    def _workspaces_root(self) -> Path:
        return self._company_root / "workspaces"

    def _agent_dir(self, agent_id: str) -> Path:
        return self._workspaces_root / agent_id

    @staticmethod
    def _validate_workspace_id(agent_id: str) -> None:
        if not agent_id or "/" in agent_id or "\\" in agent_id or agent_id in (".", ".."):
            raise ValueError(f"invalid agent_id: {agent_id!r}")

    def _resolve(self, agent_id: str, relative_path: str) -> Path:
        """Return the absolute path for ``relative_path`` inside ``agent_id``'s workspace.

        Rejects empty paths, absolute paths, ``..`` escapes, and any path that
        — after following symlinks — leaves the workspace base.
        """
        if relative_path is None or relative_path == "":
            raise PathOutsideWorkspace("relative_path must be non-empty")
        # Normalise separators and reject absolute paths up front.
        if os.path.isabs(relative_path) or relative_path.startswith(("/", "\\")):
            raise PathOutsideWorkspace(f"absolute path not allowed: {relative_path!r}")

        base = self._agent_dir(agent_id)
        if not base.exists():
            raise WorkspaceNotInitialized(agent_id)
        base_real = base.resolve(strict=True)

        # ``os.path.realpath`` follows symlinks even for non-existent leaf
        # components, which is what we need to reject symlink escapes.
        candidate = base / relative_path
        resolved = Path(os.path.realpath(candidate))

        try:
            resolved.relative_to(base_real)
        except ValueError as exc:
            raise PathOutsideWorkspace(
                f"{relative_path!r} resolves outside workspace"
            ) from exc
        return resolved

    # --------------------------------------------------------- lifecycle ops

    def create_for_agent(self, agent_id: str, *, quota_mb: int) -> None:
        """Provision an agent's workspace row + directory.

        Idempotent on the directory but raises if a row already exists, so
        callers cannot silently change a quota via this method.
        """
        if quota_mb < 0:
            raise ValueError("quota_mb must be non-negative")
        self._validate_workspace_id(agent_id)

        quota_bytes = quota_mb * 1024 * 1024
        now = datetime.now(UTC).isoformat()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO workspaces (agent_id, quota_bytes, used_bytes, created_at) "
                "VALUES (?, ?, 0, ?)",
                (agent_id, quota_bytes, now),
            )
            self._agent_dir(agent_id).mkdir(parents=True, exist_ok=True)

    def ensure_for_agent(self, agent_id: str, *, quota_mb: int) -> None:
        """Provision a workspace if missing; keep the directory present.

        Used for singleton/shared workspaces that must exist on every company
        restart. Existing rows keep their usage counter and get the requested
        quota refreshed.
        """
        if quota_mb < 0:
            raise ValueError("quota_mb must be non-negative")
        self._validate_workspace_id(agent_id)

        quota_bytes = quota_mb * 1024 * 1024
        now = datetime.now(UTC).isoformat()
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM workspaces WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO workspaces "
                    "(agent_id, quota_bytes, used_bytes, created_at) "
                    "VALUES (?, ?, 0, ?)",
                    (agent_id, quota_bytes, now),
                )
            else:
                conn.execute(
                    "UPDATE workspaces SET quota_bytes = ? WHERE agent_id = ?",
                    (quota_bytes, agent_id),
                )
            self._agent_dir(agent_id).mkdir(parents=True, exist_ok=True)

    def delete_for_agent(self, agent_id: str) -> None:
        """Remove the agent's row and directory tree. Idempotent."""
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM workspaces WHERE agent_id = ?", (agent_id,))
            agent_dir = self._agent_dir(agent_id)
            if agent_dir.exists():
                shutil.rmtree(agent_dir)

    # --------------------------------------------------------------- writes

    def write(self, agent_id: str, relative_path: str, data: bytes) -> WriteResult:
        """Write ``data`` to ``relative_path`` inside ``agent_id``'s workspace.

        Atomically updates the cached ``used_bytes`` and emits a quota event
        if the agent crosses the 90% warning threshold. Refuses to write
        through a symlink even if it points back inside the workspace.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        target = self._resolve(agent_id, relative_path)

        # Disallow writing through any symlink in the relative path. We never
        # *create* symlinks, so the only way one exists is via tampering, and
        # we treat any such write as untrusted regardless of where it points.
        base = self._agent_dir(agent_id)
        candidate = base / relative_path
        probe: Path | None = candidate
        while probe is not None and probe != base:
            if probe.is_symlink():
                raise PathOutsideWorkspace(
                    f"symlink writes are forbidden: {relative_path!r}"
                )
            probe = probe.parent if probe.parent != probe else None

        with self._fs_lock, self._db.transaction() as conn:
            row = conn.execute(
                "SELECT quota_bytes, used_bytes FROM workspaces WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            if row is None:
                raise WorkspaceNotInitialized(agent_id)
            agent_quota = int(row["quota_bytes"])
            agent_used = int(row["used_bytes"])

            company_quota_row = conn.execute(
                "SELECT quota_bytes FROM company_quota WHERE id = 1"
            ).fetchone()
            company_quota = int(company_quota_row["quota_bytes"]) if company_quota_row else 0
            company_used_row = conn.execute(
                "SELECT COALESCE(SUM(used_bytes), 0) AS s FROM workspaces"
            ).fetchone()
            company_used = int(company_used_row["s"])

            existing_size = target.stat().st_size if target.exists() else 0
            new_payload = len(data)
            new_agent_used = agent_used - existing_size + new_payload
            new_company_used = company_used - existing_size + new_payload

            if new_agent_used > agent_quota:
                self._events.emit(
                    "quota_exceeded",
                    {
                        "scope": "agent",
                        "agent_id": agent_id,
                        "limit": agent_quota,
                        "would_use": new_agent_used,
                    },
                )
                raise QuotaExceeded(
                    f"agent {agent_id!r}: {new_agent_used} > {agent_quota}"
                )
            if new_company_used > company_quota:
                self._events.emit(
                    "quota_exceeded",
                    {
                        "scope": "company",
                        "limit": company_quota,
                        "would_use": new_company_used,
                    },
                )
                raise QuotaExceeded(
                    f"company: {new_company_used} > {company_quota}"
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = target.with_name(target.name + ".tmp")
            try:
                tmp_path.write_bytes(bytes(data))
                os.replace(tmp_path, target)
            except OSError:
                if tmp_path.exists():
                    with contextlib.suppress(OSError):
                        tmp_path.unlink()
                raise

            conn.execute(
                "UPDATE workspaces SET used_bytes = ? WHERE agent_id = ?",
                (new_agent_used, agent_id),
            )

            crossed_warning = (
                agent_quota > 0
                and agent_used < _WARNING_RATIO * agent_quota
                and new_agent_used >= _WARNING_RATIO * agent_quota
            )

        if crossed_warning:
            self._events.emit(
                "quota_warning",
                {
                    "agent_id": agent_id,
                    "used": new_agent_used,
                    "limit": agent_quota,
                    "ratio": new_agent_used / agent_quota if agent_quota else 1.0,
                },
            )

        return WriteResult(
            bytes_written=new_payload,
            company_remaining_mb=(company_quota - new_company_used) / (1024 * 1024),
            agent_remaining_mb=(agent_quota - new_agent_used) / (1024 * 1024),
        )

    # ---------------------------------------------------------------- reads

    def read_own(self, agent_id: str, relative_path: str) -> bytes:
        """Read a file the agent owns."""
        target = self._resolve(agent_id, relative_path)
        if not target.is_file():
            raise FileNotFoundError(relative_path)
        return target.read_bytes()

    def read_subordinate(
        self, reader_id: str, owner_id: str, relative_path: str
    ) -> bytes:
        """Read a file in ``owner_id``'s workspace if Identity allows it."""
        if not self._identity.can_read_workspace(reader_id, owner_id):
            raise PermissionDenied(
                f"{reader_id!r} may not read workspace of {owner_id!r}"
            )
        target = self._resolve(owner_id, relative_path)
        if not target.is_file():
            raise FileNotFoundError(relative_path)
        return target.read_bytes()

    # ---------------------------------------------------------- listing/del

    def list(self, agent_id: str, relative_path: str = "") -> list[FileMeta]:
        """List files/directories under ``relative_path`` (non-recursive)."""
        base = self._agent_dir(agent_id)
        if not base.exists():
            raise WorkspaceNotInitialized(agent_id)
        target = base if relative_path == "" else self._resolve(agent_id, relative_path)
        if not target.is_dir():
            raise NotADirectoryError(relative_path)
        base_real = base.resolve(strict=True)
        out: list[FileMeta] = []
        for entry in sorted(target.iterdir()):
            stat = entry.stat()
            rel = entry.resolve().relative_to(base_real).as_posix()
            out.append(
                FileMeta(
                    relative_path=rel,
                    size=stat.st_size,
                    is_dir=entry.is_dir(),
                    modified_at=stat.st_mtime,
                )
            )
        return out

    def delete(self, agent_id: str, relative_path: str) -> None:
        """Delete a file (not a directory) and update the quota cache."""
        target = self._resolve(agent_id, relative_path)
        if target.is_dir():
            raise IsADirectoryError(relative_path)
        if not target.exists():
            raise FileNotFoundError(relative_path)
        with self._fs_lock, self._db.transaction() as conn:
            size = target.stat().st_size
            target.unlink()
            conn.execute(
                "UPDATE workspaces SET used_bytes = MAX(used_bytes - ?, 0) "
                "WHERE agent_id = ?",
                (size, agent_id),
            )

    # -------------------------------------------------------- reconciliation

    def reconcile_quota(self) -> dict[str, int]:
        """Walk each agent dir, recompute ``used_bytes`` from disk, fix drift.

        Returns a mapping ``agent_id -> drift_bytes`` for every agent whose
        cached value disagreed with reality. Emits ``quota_drift`` per such
        agent.
        """
        drift: dict[str, int] = {}
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT agent_id, used_bytes FROM workspaces"
            ).fetchall()
            for row in rows:
                agent_id = row["agent_id"]
                cached = int(row["used_bytes"])
                actual = self._dir_size(self._agent_dir(agent_id))
                if cached != actual:
                    drift[agent_id] = actual - cached
                    conn.execute(
                        "UPDATE workspaces SET used_bytes = ? WHERE agent_id = ?",
                        (actual, agent_id),
                    )
        for agent_id, delta in drift.items():
            self._events.emit(
                "quota_drift",
                {"agent_id": agent_id, "drift_bytes": delta},
            )
        return drift

    @staticmethod
    def _dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for root, _dirs, files in os.walk(path, followlinks=False):
            root_path = Path(root)
            for name in files:
                fp = root_path / name
                try:
                    total += fp.stat().st_size
                except OSError:
                    # Race against deletion or a broken link — ignore.
                    continue
        return total

    # ------------------------------------------------------------- archival

    def archive_company(self, dest: Path) -> None:
        """Move the company's whole on-disk directory to ``dest``.

        Used when a company is shut down. Caller is responsible for closing
        any DB handles first.
        """
        dest = Path(dest)
        src = self._company_root
        if not src.exists():
            raise FileNotFoundError(str(src))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise FileExistsError(str(dest))
        shutil.move(str(src), str(dest))
