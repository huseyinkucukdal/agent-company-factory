"""Append-only board audit log.

Records every state-changing action: company create/close/pause/resume,
approval decision, settings change, link decision, user CRUD, etc. The
log is the source of truth for ``GET /audit``.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules.storage import BoardDB


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class AuditEntry:
    id: int
    user_id: str
    action: str
    target: str | None
    payload: dict[str, Any]
    ts: datetime
    ip: str | None
    user_agent: str | None


class AuditLog:
    """Wrapper over the ``board_audit`` table."""

    def __init__(self, db: BoardDB) -> None:
        self._db = db

    def record(
        self,
        *,
        user_id: str,
        action: str,
        target: str | None = None,
        payload: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> int:
        """Append one entry. Returns the inserted row id."""
        body = json.dumps(
            payload or {}, separators=(",", ":"), sort_keys=True
        )
        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO board_audit "
                "(user_id, action, target, payload_json, ts, ip, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, action, target, body, _utcnow_iso(), ip, user_agent),
            )
            return int(cur.lastrowid or 0)

    def list(
        self,
        *,
        user_id: str | None = None,
        action: str | None = None,
        since: int | None = None,
        limit: int = 200,
    ) -> Iterator[AuditEntry]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if since is not None:
            clauses.append("id > ?")
            params.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, user_id, action, target, payload_json, ts, ip, user_agent "
            f"FROM board_audit{where} ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)
        rows = self._db.connect().execute(sql, params).fetchall()
        for r in rows:
            yield AuditEntry(
                id=int(r["id"]),
                user_id=r["user_id"],
                action=r["action"],
                target=r["target"],
                payload=json.loads(r["payload_json"]) if r["payload_json"] else {},
                ts=datetime.fromisoformat(r["ts"]),
                ip=r["ip"],
                user_agent=r["user_agent"],
            )


__all__ = ["AuditEntry", "AuditLog"]
