"""Board-level inter-company link administration.

This module owns the ``board_links`` table. It is the system-of-record for
"who is allowed to talk to whom". The runtime cross-company message
routing is the responsibility of Module 16 (Inter-Company Communication);
Module 16 will read from the same table.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from modules.event_store import EventKind
from modules.factory import CompanyFactory, CompanyStatus
from modules.storage import BoardDB

from .exceptions import (
    Conflict,
    LinkAlreadyExists,
    LinkNotFound,
    NotFound,
    ValidationFailed,
)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class LinkStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    DENIED = "denied"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class LinkRelationship(StrEnum):
    PEER = "peer"
    VENDOR = "vendor"
    CUSTOMER = "customer"


@dataclass(frozen=True)
class LinkScope:
    allowed_messages: tuple[str, ...] = ()
    rate_limit_per_hour: int = 100
    max_payload_bytes: int = 65_536

    def to_json(self) -> dict[str, Any]:
        return {
            "allowed_messages": list(self.allowed_messages),
            "rate_limit_per_hour": self.rate_limit_per_hour,
            "max_payload_bytes": self.max_payload_bytes,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LinkScope:
        return cls(
            allowed_messages=tuple(data.get("allowed_messages", [])),
            rate_limit_per_hour=int(data.get("rate_limit_per_hour", 100)),
            max_payload_bytes=int(data.get("max_payload_bytes", 65_536)),
        )


@dataclass(frozen=True)
class Link:
    id: str
    from_company: str
    to_company: str
    relationship: LinkRelationship
    scope: LinkScope
    status: LinkStatus
    requested_by: str
    requested_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    note: str | None = None


class LinkService:
    """CRUD + status machine for ``board_links``."""

    def __init__(self, db: BoardDB, factory: CompanyFactory) -> None:
        self._db = db
        self._factory = factory

    # ----------------------------------------------------------- helpers

    def _emit_to_company(
        self, company_id: str, kind: EventKind, payload: dict[str, Any], actor: str,
    ) -> None:
        try:
            handle = self._factory.get_handle(company_id)
        except Exception:
            return
        try:
            handle.events.append(kind, payload, actor=actor)
        except Exception:
            # Audit-log on board side already captured the action; failing
            # to mirror it into the per-company event log shouldn't bring
            # the whole request down.
            pass

    # ----------------------------------------------------------- queries

    def get(self, link_id: str) -> Link:
        row = self._db.connect().execute(
            "SELECT * FROM board_links WHERE id = ?", (link_id,),
        ).fetchone()
        if row is None:
            raise LinkNotFound(link_id)
        return _row_to_link(row)

    def list(
        self,
        *,
        company: str | None = None,
        status: LinkStatus | None = None,
    ) -> Iterator[Link]:
        clauses: list[str] = []
        params: list[Any] = []
        if company is not None:
            clauses.append("(from_company = ? OR to_company = ?)")
            params.extend([company, company])
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM board_links{where} ORDER BY requested_at DESC"
        rows = self._db.connect().execute(sql, params).fetchall()
        for r in rows:
            yield _row_to_link(r)

    # ------------------------------------------------------------ create

    def request(
        self,
        *,
        from_company: str,
        to_company: str,
        relationship: LinkRelationship,
        scope: LinkScope,
        requested_by: str,
    ) -> Link:
        if from_company == to_company:
            raise ValidationFailed("from_company_equals_to_company")
        # Both companies must exist and not be closed.
        for cid in (from_company, to_company):
            try:
                summary = self._factory.get_summary(cid)
            except Exception as exc:
                raise NotFound(f"company_not_found:{cid}") from exc
            if summary.status in (CompanyStatus.CLOSED, CompanyStatus.FAILED):
                raise Conflict(f"company_closed:{cid}")

        link_id = uuid.uuid4().hex
        scope_json = json.dumps(scope.to_json(), separators=(",", ":"))
        now = _utcnow_iso()

        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO board_links "
                    "(id, from_company, to_company, relationship, scope_json, "
                    " status, requested_by, requested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        link_id,
                        from_company,
                        to_company,
                        relationship.value,
                        scope_json,
                        LinkStatus.REQUESTED.value,
                        requested_by,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise LinkAlreadyExists(f"{from_company}->{to_company}") from exc

        link = self.get(link_id)
        # Mirror to per-company event logs (best effort).
        for cid in (from_company, to_company):
            self._emit_to_company(
                cid,
                EventKind.LINK_REQUESTED,
                {
                    "link_id": link_id,
                    "from_company": from_company,
                    "to_company": to_company,
                    "relationship": relationship.value,
                    "requested_by": requested_by,
                },
                actor=requested_by,
            )
        return link

    # ------------------------------------------------------------ decide

    def decide(self, link_id: str, *, decision: str, decided_by: str, note: str | None) -> Link:
        if decision not in ("approve", "deny"):
            raise ValidationFailed(f"unknown_decision:{decision}")
        link = self.get(link_id)
        if link.status is not LinkStatus.REQUESTED:
            raise Conflict(f"link_not_pending:{link.status.value}")
        new_status = (
            LinkStatus.APPROVED if decision == "approve" else LinkStatus.DENIED
        )
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE board_links SET status = ?, decided_by = ?, decided_at = ?, "
                "  note = ? WHERE id = ?",
                (new_status.value, decided_by, _utcnow_iso(), note, link_id),
            )
        updated = self.get(link_id)
        if new_status is LinkStatus.APPROVED:
            for cid in (link.from_company, link.to_company):
                self._emit_to_company(
                    cid,
                    EventKind.LINK_APPROVED,
                    {
                        "link_id": link_id,
                        "from_company": link.from_company,
                        "to_company": link.to_company,
                        "decided_by": decided_by,
                    },
                    actor=decided_by,
                )
        return updated

    def revoke(self, link_id: str, *, by: str, reason: str) -> Link:
        link = self.get(link_id)
        if link.status not in (LinkStatus.APPROVED, LinkStatus.SUSPENDED):
            raise Conflict(f"link_not_revocable:{link.status.value}")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE board_links SET status = ?, decided_by = ?, decided_at = ?, "
                "  note = ? WHERE id = ?",
                (LinkStatus.REVOKED.value, by, _utcnow_iso(), reason, link_id),
            )
        return self.get(link_id)

    def suspend(self, link_id: str, *, by: str, reason: str) -> Link:
        link = self.get(link_id)
        if link.status is not LinkStatus.APPROVED:
            raise Conflict(f"link_not_active:{link.status.value}")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE board_links SET status = ?, decided_by = ?, decided_at = ?, "
                "  note = ? WHERE id = ?",
                (LinkStatus.SUSPENDED.value, by, _utcnow_iso(), reason, link_id),
            )
        return self.get(link_id)

    def resume(self, link_id: str, *, by: str) -> Link:
        link = self.get(link_id)
        if link.status is not LinkStatus.SUSPENDED:
            raise Conflict(f"link_not_suspended:{link.status.value}")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE board_links SET status = ?, decided_by = ?, decided_at = ?, "
                "  note = NULL WHERE id = ?",
                (LinkStatus.APPROVED.value, by, _utcnow_iso(), link_id),
            )
        return self.get(link_id)

    def revoke_for_company(self, company_id: str, *, by: str = "system") -> int:
        """Used when a company closes — bulk-revoke its still-live links."""
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE board_links SET status = ?, decided_by = ?, decided_at = ?, "
                "  note = ? "
                "WHERE (from_company = ? OR to_company = ?) "
                "  AND status IN (?, ?)",
                (
                    LinkStatus.REVOKED.value,
                    by,
                    _utcnow_iso(),
                    "company_closed",
                    company_id,
                    company_id,
                    LinkStatus.APPROVED.value,
                    LinkStatus.SUSPENDED.value,
                ),
            )
            return int(cur.rowcount)


def _row_to_link(row: Any) -> Link:
    return Link(
        id=row["id"],
        from_company=row["from_company"],
        to_company=row["to_company"],
        relationship=LinkRelationship(row["relationship"]),
        scope=LinkScope.from_json(json.loads(row["scope_json"])),
        status=LinkStatus(row["status"]),
        requested_by=row["requested_by"],
        requested_at=datetime.fromisoformat(row["requested_at"]),
        decided_by=row["decided_by"],
        decided_at=(
            datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None
        ),
        note=row["note"],
    )


__all__ = [
    "Link",
    "LinkRelationship",
    "LinkScope",
    "LinkService",
    "LinkStatus",
]
