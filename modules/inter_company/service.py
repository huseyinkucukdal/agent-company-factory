"""Inter-Company runtime service.

Reads the board's link catalogue (owned by Module 14
``modules.board_api.links``) and routes cross-company messages into the
target company's orchestrator via ``system_send``. Per-link rate limit,
scope check and payload sanitisation live here. A persistent outbox in
the board DB records every attempt for audit and pause-recovery.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules.agent_runtime import MessageKind
from modules.board_api.links import LinkService, LinkStatus
from modules.event_store import EventKind
from modules.factory import CompanyFactory, CompanyStatus
from modules.identity import Role, Status
from modules.orchestrator import SendResult
from modules.storage import BoardDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .exceptions import (
    CrossRateLimited,
    DirectionMismatch,
    InterCompanyError,
    LinkNotApproved,
    LinkNotFound,
    PayloadTooLarge,
    ScopeViolation,
    TargetUnavailable,
)
from .models import (
    CrossEntry,
    CrossMessage,
    CrossMessageKind,
    CrossResult,
    CrossStatus,
)

_log = logging.getLogger(__name__)
_MODULE_KEY = "inter_company"

_HOUR_SECONDS = 3600.0
_MAX_SUBJECT_BYTES = 512
# Roles that may receive cross-company messages, in fallback order.
_INBOX_ROLES: tuple[Role, ...] = (Role.HR, Role.CEO)
# Strip ASCII control characters except common whitespace (\t \n \r).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _sanitize(text: str, *, max_bytes: int) -> str:
    cleaned = _CTRL_RE.sub("", text)
    encoded = cleaned.encode("utf-8")
    if len(encoded) > max_bytes:
        # Truncate on a UTF-8 boundary by re-encoding the prefix.
        cleaned = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return cleaned


@dataclass
class _LinkBucket:
    """Sliding-window per-link rate limiter (last hour of timestamps)."""

    timestamps: deque[float]


class InterCompanyService:
    """Cross-company runtime router.

    Lifecycle expectations:
        * Both source and target ``CompanyHandle`` instances must be
          loaded in the supplied ``CompanyFactory`` (raise
          :class:`TargetUnavailable` otherwise).
        * The ``LinkService`` is the source of truth for link state and
          scope; this service mutates it only via :meth:`on_company_closed`.
    """

    def __init__(
        self,
        *,
        board_db: BoardDB,
        factory: CompanyFactory,
        links: LinkService,
    ) -> None:
        self._db = board_db
        self._factory = factory
        self._links = links
        self._buckets: dict[str, _LinkBucket] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ schema

    def migrate(self) -> None:
        migrations = load_migrations_from_package(
            "modules.inter_company.migrations"
        )
        apply_migrations(
            self._db.connect(), module=_MODULE_KEY, migrations=migrations,
        )

    # --------------------------------------------------------- main API

    async def send_cross(
        self,
        *,
        link_id: str,
        from_agent: str,
        from_company: str,
        message: CrossMessage,
        request_id: str | None = None,
    ) -> CrossResult:
        """Validate, sanitize and route a cross-company message.

        On every outcome an outbox row is persisted. Raises only on
        truly unexpected failures; permission/scope/rate problems are
        recorded as ``CrossStatus.REJECTED`` and surfaced as a
        :class:`CrossResult` *and* as a corresponding exception so
        callers can choose either style. The connector adapter prefers
        the result form to keep agent-facing surfaces declarative.
        """
        link = self._lookup_link(link_id)
        self._enforce_direction(link, from_company)
        self._enforce_status(link)
        self._enforce_scope(link, message)
        self._enforce_rate(link)

        target_company = link.to_company
        target_handle = self._get_target_handle(target_company)
        target_agent = self._resolve_inbox_agent(target_company)

        rid = request_id or uuid.uuid4().hex
        scope_max = link.scope.max_payload_bytes
        sanitized_subject = _sanitize(
            message.subject, max_bytes=_MAX_SUBJECT_BYTES,
        )
        sanitized_body = _sanitize(message.body, max_bytes=scope_max)

        # Compose envelope content for the target agent. Includes a
        # stable header so the recipient (HR/CEO) can quickly identify
        # the source without parsing free-form prose.
        content = self._format_envelope(
            link_id=link_id,
            from_company=from_company,
            kind=message.kind,
            subject=sanitized_subject,
            body=sanitized_body,
        )

        if target_agent is None:
            return self._record_and_emit(
                rid=rid,
                link_id=link_id,
                link=link,
                from_agent=from_agent,
                target_agent=None,
                message=message,
                sanitized_body=sanitized_body,
                sanitized_subject=sanitized_subject,
                status=CrossStatus.REJECTED,
                reason="no_inbox_agent",
            )

        # Attempt delivery via the target orchestrator. The orchestrator
        # itself returns SendResult.REJECTED_PAUSED when the company's
        # clock is paused — we map that to QUEUED.
        try:
            result = await target_handle.orchestrator.system_send(
                target_agent,
                content,
                kind=MessageKind.USER_REQUEST,
                correlation_id=rid,
            )
        except Exception as exc:  # pragma: no cover — defensive
            _log.exception("inter_company send failed: %s", exc)
            return self._record_and_emit(
                rid=rid,
                link_id=link_id,
                link=link,
                from_agent=from_agent,
                target_agent=target_agent,
                message=message,
                sanitized_body=sanitized_body,
                sanitized_subject=sanitized_subject,
                status=CrossStatus.REJECTED,
                reason="delivery_error",
            )

        status = self._map_result(result)
        reason = None if status is not CrossStatus.REJECTED else result.value
        return self._record_and_emit(
            rid=rid,
            link_id=link_id,
            link=link,
            from_agent=from_agent,
            target_agent=target_agent,
            message=message,
            sanitized_body=sanitized_body,
            sanitized_subject=sanitized_subject,
            status=status,
            reason=reason,
        )

    # ----------------------------------------------------- pause replay

    async def deliver_pending(self, company_id: str) -> int:
        """Re-attempt all queued messages addressed to ``company_id``.

        Called after a target company resumes from pause. Returns the
        number of rows whose status transitioned from QUEUED to either
        DELIVERED or REJECTED.
        """
        rows = self._db.connect().execute(
            "SELECT id, link_id, from_company, from_agent, target_agent, "
            "       kind, subject, body, metadata_json "
            "FROM inter_company_outbox "
            "WHERE to_company = ? AND status = ? "
            "ORDER BY created_at ASC",
            (company_id, CrossStatus.QUEUED.value),
        ).fetchall()

        flushed = 0
        for row in rows:
            try:
                target_handle = self._get_target_handle(company_id)
            except TargetUnavailable:
                break
            link = self._lookup_link(row["link_id"])
            if link.status is not LinkStatus.APPROVED:
                self._update_status(
                    row["id"], CrossStatus.REJECTED, reason="link_not_approved",
                )
                flushed += 1
                continue
            target_agent = row["target_agent"] or self._resolve_inbox_agent(
                company_id,
            )
            if target_agent is None:
                self._update_status(
                    row["id"], CrossStatus.REJECTED, reason="no_inbox_agent",
                )
                flushed += 1
                continue
            content = self._format_envelope(
                link_id=row["link_id"],
                from_company=row["from_company"],
                kind=CrossMessageKind(row["kind"]),
                subject=row["subject"],
                body=row["body"],
            )
            try:
                result = await target_handle.orchestrator.system_send(
                    target_agent,
                    content,
                    kind=MessageKind.USER_REQUEST,
                    correlation_id=row["id"],
                )
            except Exception:  # pragma: no cover — defensive
                _log.exception("inter_company replay failed for %s", row["id"])
                continue
            status = self._map_result(result)
            if status is CrossStatus.QUEUED:
                continue  # still paused — leave for next flush
            self._update_status(
                row["id"],
                status,
                reason=None if status is CrossStatus.DELIVERED else result.value,
                target_agent=target_agent,
            )
            flushed += 1
        return flushed

    # -------------------------------------------------- queries / hooks

    def list_outbox(
        self,
        *,
        link_id: str | None = None,
        company: str | None = None,
        status: CrossStatus | None = None,
        limit: int = 100,
    ) -> list[CrossEntry]:
        clauses: list[str] = []
        params: list[Any] = []
        if link_id is not None:
            clauses.append("link_id = ?")
            params.append(link_id)
        if company is not None:
            clauses.append("(from_company = ? OR to_company = ?)")
            params.extend([company, company])
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT * FROM inter_company_outbox{where} "
            "ORDER BY created_at DESC LIMIT ?"
        )
        params.append(limit)
        rows = self._db.connect().execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def on_company_closed(self, company_id: str, *, by: str = "system") -> int:
        """Auto-revoke all live links when a company closes.

        Mirrors :meth:`LinkService.revoke_for_company`; provided here so
        callers (e.g. ``CompanyFactory.close_company``) can wire one
        hook. Returns the number of links revoked.
        """
        return self._links.revoke_for_company(company_id, by=by)

    # -------------------------------------------------------- internals

    def _lookup_link(self, link_id: str) -> Any:
        try:
            return self._links.get(link_id)
        except Exception as exc:
            raise LinkNotFound(link_id) from exc

    def _enforce_direction(self, link: Any, from_company: str) -> None:
        if link.from_company != from_company:
            raise DirectionMismatch(
                f"link {link.id} is {link.from_company}->{link.to_company}, "
                f"caller is {from_company}",
            )

    def _enforce_status(self, link: Any) -> None:
        if link.status is not LinkStatus.APPROVED:
            raise LinkNotApproved(f"link {link.id} status={link.status.value}")

    def _enforce_scope(self, link: Any, message: CrossMessage) -> None:
        allowed = set(link.scope.allowed_messages)
        # Empty allowed_messages means "all kinds permitted".
        if allowed and message.kind.value not in allowed:
            raise ScopeViolation(
                f"kind {message.kind.value} not in {sorted(allowed)}",
            )
        body_bytes = len(message.body.encode("utf-8"))
        if body_bytes > link.scope.max_payload_bytes:
            raise PayloadTooLarge(
                f"{body_bytes} > {link.scope.max_payload_bytes}",
            )

    def _enforce_rate(self, link: Any) -> None:
        cap = max(0, int(link.scope.rate_limit_per_hour))
        if cap == 0:
            return
        now = time.monotonic()
        cutoff = now - _HOUR_SECONDS
        with self._lock:
            bucket = self._buckets.get(link.id)
            if bucket is None:
                bucket = _LinkBucket(timestamps=deque())
                self._buckets[link.id] = bucket
            while bucket.timestamps and bucket.timestamps[0] < cutoff:
                bucket.timestamps.popleft()
            if len(bucket.timestamps) >= cap:
                raise CrossRateLimited(
                    f"link {link.id}: {cap} msg/hour exceeded",
                )
            bucket.timestamps.append(now)

    def _get_target_handle(self, company_id: str) -> Any:
        try:
            summary = self._factory.get_summary(company_id)
        except Exception as exc:
            raise TargetUnavailable(company_id) from exc
        if summary.status in (CompanyStatus.CLOSED, CompanyStatus.FAILED):
            raise TargetUnavailable(f"{company_id}:{summary.status.value}")
        try:
            return self._factory.get_handle(company_id)
        except Exception as exc:
            raise TargetUnavailable(company_id) from exc

    def _resolve_inbox_agent(self, company_id: str) -> str | None:
        try:
            handle = self._factory.get_handle(company_id)
        except Exception:
            return None
        actives = handle.identity.all(status=Status.ACTIVE)
        for role in _INBOX_ROLES:
            for a in actives:
                if a.role == role:
                    return a.id
        return None

    def _format_envelope(
        self,
        *,
        link_id: str,
        from_company: str,
        kind: CrossMessageKind,
        subject: str,
        body: str,
    ) -> str:
        return (
            f"[INTER-COMPANY {kind.value} | from={from_company} "
            f"| link={link_id}]\n"
            f"Subject: {subject}\n"
            f"\n"
            f"{body}"
        )

    def _map_result(self, result: SendResult) -> CrossStatus:
        if result is SendResult.QUEUED:
            return CrossStatus.DELIVERED
        if result is SendResult.REJECTED_PAUSED:
            return CrossStatus.QUEUED
        return CrossStatus.REJECTED

    def _record_and_emit(
        self,
        *,
        rid: str,
        link_id: str,
        link: Any,
        from_agent: str,
        target_agent: str | None,
        message: CrossMessage,
        sanitized_body: str,
        sanitized_subject: str,
        status: CrossStatus,
        reason: str | None,
    ) -> CrossResult:
        now = _utcnow()
        delivered_at = now if status is CrossStatus.DELIVERED else None
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO inter_company_outbox "
                "(id, link_id, from_company, to_company, from_agent, "
                " target_agent, kind, subject, body, metadata_json, "
                " status, reason, created_at, delivered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    link_id,
                    link.from_company,
                    link.to_company,
                    from_agent,
                    target_agent,
                    message.kind.value,
                    sanitized_subject,
                    sanitized_body,
                    json.dumps(message.metadata, separators=(",", ":")),
                    status.value,
                    reason,
                    now.isoformat(),
                    delivered_at.isoformat() if delivered_at else None,
                ),
            )
        # Mirror to per-company event logs (best effort).
        for cid in (link.from_company, link.to_company):
            self._emit_event(
                cid,
                EventKind.EXTERNAL_CALL,
                {
                    "channel": "inter_company",
                    "outbox_id": rid,
                    "link_id": link_id,
                    "direction": (
                        "outbound" if cid == link.from_company else "inbound"
                    ),
                    "kind": message.kind.value,
                    "status": status.value,
                    "subject": sanitized_subject,
                    "reason": reason,
                },
                actor=from_agent,
            )
        return CrossResult(
            id=rid,
            link_id=link_id,
            status=status,
            target_agent=target_agent,
            reason=reason,
        )

    def _emit_event(
        self,
        company_id: str,
        kind: EventKind,
        payload: dict[str, Any],
        actor: str,
    ) -> None:
        try:
            handle = self._factory.get_handle(company_id)
        except Exception:
            return
        try:
            handle.events.append(kind, payload, actor=actor)
        except Exception:  # pragma: no cover — best-effort mirror
            _log.exception("inter_company event mirror failed for %s", company_id)

    def _update_status(
        self,
        outbox_id: str,
        status: CrossStatus,
        *,
        reason: str | None,
        target_agent: str | None = None,
    ) -> None:
        delivered_at = (
            _utcnow_iso() if status is CrossStatus.DELIVERED else None
        )
        with self._db.transaction() as conn:
            if target_agent is not None:
                conn.execute(
                    "UPDATE inter_company_outbox "
                    "SET status = ?, reason = ?, delivered_at = ?, "
                    "    target_agent = ? "
                    "WHERE id = ?",
                    (status.value, reason, delivered_at, target_agent, outbox_id),
                )
            else:
                conn.execute(
                    "UPDATE inter_company_outbox "
                    "SET status = ?, reason = ?, delivered_at = ? "
                    "WHERE id = ?",
                    (status.value, reason, delivered_at, outbox_id),
                )


# --------------------------------------------------------------- helpers

def _row_to_entry(row: Any) -> CrossEntry:
    return CrossEntry(
        id=row["id"],
        link_id=row["link_id"],
        from_company=row["from_company"],
        to_company=row["to_company"],
        from_agent=row["from_agent"],
        target_agent=row["target_agent"],
        kind=CrossMessageKind(row["kind"]),
        subject=row["subject"],
        body=row["body"],
        metadata=json.loads(row["metadata_json"] or "{}"),
        status=CrossStatus(row["status"]),
        reason=row["reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
        delivered_at=(
            datetime.fromisoformat(row["delivered_at"])
            if row["delivered_at"]
            else None
        ),
    )


__all__ = [
    "InterCompanyError",
    "InterCompanyService",
]
