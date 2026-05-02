"""The :class:`Approvals` service.

Concurrency: every state-changing method runs inside a single
``CompanyDB.transaction()`` (BEGIN IMMEDIATE) which serialises writers.
Subscriber callbacks and event emissions happen **after** commit so a
crashing callback cannot abort the underlying write.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from modules.event_store import EventKind, EventStore
from modules.storage import CompanyDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .exceptions import (
    ApprovalConflict,
    ApprovalNotFound,
    InvalidTransition,
)
from .models import (
    Approval,
    ApprovalKind,
    ApprovalRoute,
    ApprovalStatus,
    Decision,
    RouteTarget,
    SubscriptionHandle,
)
from .routing import (
    AUTO_DENY_ON_TIMEOUT,
    DEFAULT_REQUIRE_SECURITY,
    DEFAULT_TIMEOUTS,
)

_log = logging.getLogger(__name__)
_MODULE_KEY = "approvals"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def _parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


_DECISION_EVENT_VALUE = {
    Decision.APPROVE: "approved",
    Decision.DENY: "rejected",
}


class Approvals:
    """Per-company approval queue + state machine."""

    def __init__(
        self,
        db: CompanyDB,
        events: EventStore,
        *,
        time_provider: Callable[[], datetime] | None = None,
        default_timeouts: dict[ApprovalKind, timedelta] | None = None,
        require_security_kinds: frozenset[ApprovalKind] | None = None,
        auto_deny_on_timeout: frozenset[ApprovalKind] | None = None,
    ) -> None:
        self._db = db
        self._events = events
        self._now = time_provider or _utcnow
        self._timeouts = dict(DEFAULT_TIMEOUTS)
        if default_timeouts:
            self._timeouts.update(default_timeouts)
        self._require_security_kinds = (
            require_security_kinds
            if require_security_kinds is not None
            else DEFAULT_REQUIRE_SECURITY
        )
        self._auto_deny_kinds = (
            auto_deny_on_timeout
            if auto_deny_on_timeout is not None
            else AUTO_DENY_ON_TIMEOUT
        )

        self._sub_lock = threading.Lock()
        self._subscribers: dict[str, Callable[[Approval], None]] = {}

    # --------------------------------------------------------------- migrate

    def migrate(self) -> None:
        migrations = load_migrations_from_package(
            "modules.approvals.migrations"
        )
        apply_migrations(
            self._db.connect(), module=_MODULE_KEY, migrations=migrations
        )

    # --------------------------------------------------------------- request

    def request(
        self,
        *,
        kind: ApprovalKind,
        requester_id: str,
        payload: dict[str, Any],
        route: ApprovalRoute,
        request_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> Approval:
        """Create (or return existing) approval. Idempotent on ``request_id``."""
        rid = request_id or _new_id()
        now = self._now()
        timeout = (
            timedelta(seconds=timeout_seconds)
            if timeout_seconds is not None
            else self._timeouts.get(kind, timedelta(hours=24))
        )
        expires_at = now + timeout

        require_security = route.require_security or (
            kind in self._require_security_kinds
            and route.target is not RouteTarget.SECURITY
        )

        emitted = False
        with self._db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM approvals WHERE request_id = ?", (rid,)
            ).fetchone()
            if existing is not None:
                return _row_to_approval(existing)

            conn.execute(
                "INSERT INTO approvals "
                "(request_id, kind, requester_id, payload_json, route_target, "
                " require_security, status, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    kind.value,
                    requester_id,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    route.encode_target(),
                    1 if require_security else 0,
                    ApprovalStatus.PENDING.value,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            emitted = True

        approval = Approval(
            request_id=rid,
            kind=kind,
            requester_id=requester_id,
            payload=dict(payload),
            route=ApprovalRoute(
                target=route.target,
                agent_id=route.agent_id,
                require_security=require_security,
            ),
            status=ApprovalStatus.PENDING,
            expires_at=expires_at,
            created_at=now,
        )

        if emitted:
            self._events.append(
                EventKind.APPROVAL_REQUESTED,
                {
                    "approval_id": rid,
                    "requested_by": requester_id,
                    "subject": kind.value,
                    "details": dict(payload),
                },
                actor=requester_id,
            )
            self._broadcast(approval)
        return approval

    # ---------------------------------------------------------------- decide

    def decide(
        self,
        request_id: str,
        decider_id: str,
        decision: Decision,
        note: str | None = None,
    ) -> Approval:
        """Apply a decision. Handles security pre-veto step transparently."""
        now = self._now()
        emitted_kind: EventKind | None = None
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise ApprovalNotFound(request_id)

            current = ApprovalStatus(row["status"])
            require_security = bool(row["require_security"])
            sec_decision_raw: str | None = row["security_decision"]

            # Already decided → idempotent on equal outcome, conflict otherwise.
            if current is not ApprovalStatus.PENDING:
                target = (
                    ApprovalStatus.APPROVED
                    if decision is Decision.APPROVE
                    else ApprovalStatus.DENIED
                )
                if current is target:
                    return _row_to_approval(row)
                raise ApprovalConflict(
                    f"already_{current.value}:{request_id}"
                )

            # Security pre-veto step.
            in_security_step = (
                require_security and sec_decision_raw is None
            )
            if in_security_step:
                if decision is Decision.DENY:
                    conn.execute(
                        "UPDATE approvals SET "
                        "  security_decision = ?, security_decided_at = ?, "
                        "  status = ?, decided_by = ?, decided_at = ?, "
                        "  note = ? "
                        "WHERE request_id = ?",
                        (
                            decision.value,
                            now.isoformat(),
                            ApprovalStatus.DENIED.value,
                            decider_id,
                            now.isoformat(),
                            note,
                            request_id,
                        ),
                    )
                    emitted_kind = EventKind.APPROVAL_DECIDED
                else:
                    conn.execute(
                        "UPDATE approvals SET "
                        "  security_decision = ?, security_decided_at = ? "
                        "WHERE request_id = ?",
                        (
                            decision.value,
                            now.isoformat(),
                            request_id,
                        ),
                    )
                    # Main approval still PENDING; no decided event yet.
            else:
                # Final decision on the route's main leg.
                final_status = (
                    ApprovalStatus.APPROVED
                    if decision is Decision.APPROVE
                    else ApprovalStatus.DENIED
                )
                conn.execute(
                    "UPDATE approvals SET "
                    "  status = ?, decided_by = ?, decided_at = ?, note = ? "
                    "WHERE request_id = ?",
                    (
                        final_status.value,
                        decider_id,
                        now.isoformat(),
                        note,
                        request_id,
                    ),
                )
                emitted_kind = EventKind.APPROVAL_DECIDED

            new_row = conn.execute(
                "SELECT * FROM approvals WHERE request_id = ?", (request_id,)
            ).fetchone()

        approval = _row_to_approval(new_row)

        if emitted_kind is EventKind.APPROVAL_DECIDED:
            self._events.append(
                EventKind.APPROVAL_DECIDED,
                {
                    "approval_id": request_id,
                    "decided_by": decider_id,
                    "decision": _DECISION_EVENT_VALUE[decision],
                    "note": note,
                },
                actor=decider_id,
            )
        self._broadcast(approval)
        return approval

    # ---------------------------------------------------------------- cancel

    def cancel(self, request_id: str, by: str, reason: str) -> Approval:
        now = self._now()
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise ApprovalNotFound(request_id)
            current = ApprovalStatus(row["status"])
            if current is not ApprovalStatus.PENDING:
                raise InvalidTransition(
                    f"cannot_cancel_{current.value}:{request_id}"
                )
            conn.execute(
                "UPDATE approvals SET status = ?, decided_by = ?, "
                "  decided_at = ?, note = ? WHERE request_id = ?",
                (
                    ApprovalStatus.CANCELLED.value,
                    by,
                    now.isoformat(),
                    reason,
                    request_id,
                ),
            )
            new_row = conn.execute(
                "SELECT * FROM approvals WHERE request_id = ?", (request_id,)
            ).fetchone()

        approval = _row_to_approval(new_row)
        self._events.append(
            EventKind.APPROVAL_DECIDED,
            {
                "approval_id": request_id,
                "decided_by": by,
                "decision": "rejected",
                "note": f"cancelled:{reason}",
            },
            actor=by,
        )
        self._broadcast(approval)
        return approval

    # ----------------------------------------------------------------- query

    def get(self, request_id: str) -> Approval:
        row = self._db.connect().execute(
            "SELECT * FROM approvals WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise ApprovalNotFound(request_id)
        return _row_to_approval(row)

    def pending_for(self, decider: ApprovalRoute) -> list[Approval]:
        """Return approvals currently awaiting *this* decider's action."""
        if decider.target is RouteTarget.SECURITY:
            rows = self._db.connect().execute(
                "SELECT * FROM approvals "
                "WHERE status = 'pending' "
                "  AND require_security = 1 "
                "  AND security_decision IS NULL "
                "ORDER BY created_at"
            ).fetchall()
        else:
            rows = self._db.connect().execute(
                "SELECT * FROM approvals "
                "WHERE status = 'pending' "
                "  AND route_target = ? "
                "  AND (require_security = 0 OR security_decision IS NOT NULL) "
                "ORDER BY created_at",
                (decider.encode_target(),),
            ).fetchall()
        return [_row_to_approval(r) for r in rows]

    def is_blocked(self, agent_id: str) -> bool:
        row = self._db.connect().execute(
            "SELECT 1 FROM approvals "
            "WHERE requester_id = ? AND status = 'pending' LIMIT 1",
            (agent_id,),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------ subscribe

    def subscribe(
        self, callback: Callable[[Approval], None]
    ) -> SubscriptionHandle:
        sub_id = _new_id()
        with self._sub_lock:
            self._subscribers[sub_id] = callback
        return SubscriptionHandle(id=sub_id)

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        with self._sub_lock:
            self._subscribers.pop(handle.id, None)

    def _broadcast(self, approval: Approval) -> None:
        with self._sub_lock:
            subs = list(self._subscribers.values())
        for cb in subs:
            try:
                cb(approval)
            except Exception:
                _log.exception("approval subscriber raised")

    # ---------------------------------------------------------------- timeout

    def process_timeouts(self) -> int:
        """Advance any approvals whose ``expires_at`` has passed.

        Returns the number of approvals transitioned. Idempotent.
        """
        now = self._now()
        timed_out: list[Approval] = []
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals "
                "WHERE status = 'pending' AND expires_at <= ?",
                (now.isoformat(),),
            ).fetchall()
            for row in rows:
                kind = ApprovalKind(row["kind"])
                if kind in self._auto_deny_kinds:
                    new_status = ApprovalStatus.DENIED
                    note = "auto_deny_on_timeout"
                else:
                    new_status = ApprovalStatus.TIMEOUT
                    note = "expired"
                conn.execute(
                    "UPDATE approvals SET status = ?, decided_at = ?, "
                    "  note = COALESCE(note, ?) WHERE request_id = ?",
                    (
                        new_status.value,
                        now.isoformat(),
                        note,
                        row["request_id"],
                    ),
                )
                refreshed = conn.execute(
                    "SELECT * FROM approvals WHERE request_id = ?",
                    (row["request_id"],),
                ).fetchone()
                timed_out.append(_row_to_approval(refreshed))

        for approval in timed_out:
            self._events.append(
                EventKind.APPROVAL_TIMEOUT,
                {"approval_id": approval.request_id},
            )
            self._broadcast(approval)
        return len(timed_out)

    async def run_timeout_loop(self, *, interval_seconds: float = 10.0) -> None:
        """Background coroutine: poll ``process_timeouts`` periodically."""
        while True:
            try:
                self.process_timeouts()
            except Exception:
                _log.exception("approvals timeout loop iteration failed")
            await asyncio.sleep(interval_seconds)


# ----------------------------------------------------------------------- helpers


def _row_to_approval(row: Any) -> Approval:
    payload_raw: str = row["payload_json"]
    payload: dict[str, Any] = json.loads(payload_raw) if payload_raw else {}
    require_security = bool(row["require_security"])
    route = ApprovalRoute.decode(
        row["route_target"], require_security=require_security
    )
    sec_raw: str | None = row["security_decision"]
    return Approval(
        request_id=row["request_id"],
        kind=ApprovalKind(row["kind"]),
        requester_id=row["requester_id"],
        payload=payload,
        route=route,
        status=ApprovalStatus(row["status"]),
        decided_by=row["decided_by"],
        decided_at=_parse_dt(row["decided_at"]),
        note=row["note"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        security_decision=Decision(sec_raw) if sec_raw else None,
        security_decided_at=_parse_dt(row["security_decided_at"]),
    )
