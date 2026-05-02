"""Quota: read/update agent and company disk limits.

Used bytes are cached in `workspaces.used_bytes`; the `Workspace` module updates
that cache during writes/deletes. `Workspace.reconcile_quota()` reconciles cache
against the real filesystem.
"""
from __future__ import annotations

from dataclasses import dataclass

from .db import CompanyDB
from .exceptions import WorkspaceNotInitialized


@dataclass(frozen=True)
class CompanyQuota:
    """Scope referring to the company's total quota."""


@dataclass(frozen=True)
class AgentQuota:
    """Scope referring to a single agent's quota."""

    agent_id: str


QuotaScope = CompanyQuota | AgentQuota


class Quota:
    def __init__(self, db: CompanyDB) -> None:
        self._db = db

    def usage(self, scope: QuotaScope) -> int:
        conn = self._db.connect()
        if isinstance(scope, CompanyQuota):
            row = conn.execute(
                "SELECT COALESCE(SUM(used_bytes), 0) FROM workspaces"
            ).fetchone()
            return int(row[0])
        if isinstance(scope, AgentQuota):
            row = conn.execute(
                "SELECT used_bytes FROM workspaces WHERE agent_id = ?",
                (scope.agent_id,),
            ).fetchone()
            return int(row[0]) if row else 0
        raise TypeError(f"unknown QuotaScope: {scope!r}")

    def limit(self, scope: QuotaScope) -> int:
        conn = self._db.connect()
        if isinstance(scope, CompanyQuota):
            row = conn.execute(
                "SELECT quota_bytes FROM company_quota WHERE id = 1"
            ).fetchone()
            return int(row[0]) if row else 0
        if isinstance(scope, AgentQuota):
            row = conn.execute(
                "SELECT quota_bytes FROM workspaces WHERE agent_id = ?",
                (scope.agent_id,),
            ).fetchone()
            return int(row[0]) if row else 0
        raise TypeError(f"unknown QuotaScope: {scope!r}")

    def remaining(self, scope: QuotaScope) -> int:
        return self.limit(scope) - self.usage(scope)

    def set_limit(self, scope: QuotaScope, *, mb: int) -> None:
        if mb < 0:
            raise ValueError("mb must be non-negative")
        bytes_value = mb * 1024 * 1024
        with self._db.transaction() as conn:
            if isinstance(scope, CompanyQuota):
                conn.execute(
                    "UPDATE company_quota SET quota_bytes = ? WHERE id = 1",
                    (bytes_value,),
                )
            elif isinstance(scope, AgentQuota):
                cur = conn.execute(
                    "UPDATE workspaces SET quota_bytes = ? WHERE agent_id = ?",
                    (bytes_value, scope.agent_id),
                )
                if cur.rowcount == 0:
                    raise WorkspaceNotInitialized(scope.agent_id)
            else:
                raise TypeError(f"unknown QuotaScope: {scope!r}")
