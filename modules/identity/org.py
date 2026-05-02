"""The :class:`Org` orchestrator — central API for identity and the org tree.

Concurrency: every state-changing method runs inside a single
``CompanyDB.transaction()`` (BEGIN IMMEDIATE) which serialises writers.
Read methods use the same per-thread connection but stay outside an explicit
transaction.

Side effects:
- ``add_agent``  → ``AGENT_HIRED`` (or ``AGENT_CREATED`` for bootstrap)
- ``fire``       → ``AGENT_FIRED``, optionally ``AGENT_FIRE_NOTIFY``,
                   ``AGENT_ORPHANED`` (one per orphaned subordinate)
- ``complete_fire`` (on APPROVED) → same suite as ``fire``'s direct path
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.event_store import EventKind, EventStore
from modules.storage import CompanyDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .exceptions import CycleDetected, FireDenied, HireDenied, SpecialRoleProtected
from .models import (
    SPECIAL_ROLES,
    Agent,
    ApprovalDecision,
    FireOutcome,
    FireResult,
    Role,
    Status,
)
from .protocols import ApprovalRequester

_MODULE_KEY = "identity"
MAX_ACTIVE_AGENTS = 100
MAX_CEO_DIRECT_REPORTS = 5
MAX_MANAGER_DIRECT_REPORTS = 4
_MIN_FIRE_REASON_CHARS = 8
_INVALID_FIRE_REASON_PHRASES = (
    "this is a test",
    "test to check",
    "system functionality",
    "no reason",
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


def _parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _direct_report_limit(role: Role) -> int:
    return MAX_CEO_DIRECT_REPORTS if role is Role.CEO else MAX_MANAGER_DIRECT_REPORTS


def _validate_fire_reason(reason: str) -> str | None:
    cleaned = " ".join(reason.split())
    if len(cleaned) < _MIN_FIRE_REASON_CHARS:
        return f"fire_reason_too_short:{_MIN_FIRE_REASON_CHARS}"
    lowered = cleaned.lower()
    if lowered in {"n/a", "na", "none", "test"}:
        return "fire_reason_invalid"
    if any(phrase in lowered for phrase in _INVALID_FIRE_REASON_PHRASES):
        return "fire_reason_invalid"
    return None


class Org:
    """Identity & org-chart service for one company."""

    def __init__(
        self,
        db: CompanyDB,
        events: EventStore,
        approvals: ApprovalRequester,
    ) -> None:
        self._db = db
        self._events = events
        self._approvals = approvals

    # --------------------------------------------------------------- migrate

    def migrate(self) -> None:
        migrations = load_migrations_from_package("modules.identity.migrations")
        apply_migrations(
            self._db.connect(), module=_MODULE_KEY, migrations=migrations
        )

    # ---------------------------------------------------------------- CRUD

    def add_agent(
        self,
        *,
        role: Role,
        persona_ref: str,
        reports_to: str | None,
        requested_by: str,
        via_hr: bool,
        bootstrap: bool = False,
        first_name: str = "",
        last_name: str = "",
        role_title: str | None = None,
        role_description: str | None = None,
    ) -> Agent:
        """Insert a new agent.

        ``via_hr`` must be true unless ``bootstrap=True`` (Factory only).
        Cycle prevention is automatic — a new id cannot point at itself, and
        ``reports_to`` must reference an existing active agent.

        Companies may have at most 100 active agents. The CEO may have at
        most 5 active direct reports; every other manager may have at most 4.
        """
        if not bootstrap and not via_hr:
            raise HireDenied("must_route_via_hr")
        if not bootstrap and reports_to is None:
            raise HireDenied("manager_required")

        agent_id = _new_id()
        now = _utcnow()

        with self._db.transaction() as conn:
            active_count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM agents WHERE status = ?",
                (Status.ACTIVE.value,),
            ).fetchone()
            if active_count["cnt"] >= MAX_ACTIVE_AGENTS:
                raise HireDenied(f"company_headcount_limit_exceeded:{MAX_ACTIVE_AGENTS}")

            if reports_to is not None:
                row = conn.execute(
                    "SELECT id, status, role FROM agents WHERE id = ?",
                    (reports_to,),
                ).fetchone()
                if row is None:
                    raise HireDenied(f"manager_not_found:{reports_to}")
                if row["status"] != Status.ACTIVE.value:
                    raise HireDenied(f"manager_not_active:{reports_to}")
                if row["id"] == agent_id:  # paranoia, ids are fresh uuids
                    raise CycleDetected("self_report")
                parent_role = Role(row["role"])
                # HR is a people-ops function; it must not manage product
                # builders. Without this, HR will keep selecting itself as
                # `reports_to` (it has capacity) and we end up with engineers
                # / designers / PMs hanging off HR — a structural nonsense.
                # Builders go to the CEO, or to a dedicated builder-side
                # manager (Tech Lead, Engineering Manager, etc.) once the
                # team grows. HR's only direct reports are HR roles.
                if (
                    not bootstrap
                    and parent_role is Role.HR
                    and role is not Role.HR
                ):
                    raise HireDenied(
                        f"hr_can_only_manage_hr:{reports_to}"
                    )
                limit = _direct_report_limit(parent_role)
                count_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM agents "
                    "WHERE reports_to = ? AND status = ?",
                    (reports_to, Status.ACTIVE.value),
                ).fetchone()
                if count_row["cnt"] >= limit:
                    raise HireDenied(
                        f"direct_report_limit_exceeded:{reports_to}:{limit}"
                    )

            conn.execute(
                "INSERT INTO agents "
                "(id, role, persona_ref, reports_to, status, hired_at, "
                " first_name, last_name, role_title, role_description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    role.value,
                    persona_ref,
                    reports_to,
                    Status.ACTIVE.value,
                    now.isoformat(),
                    first_name,
                    last_name,
                    role_title,
                    role_description,
                ),
            )

        kind = EventKind.AGENT_CREATED if bootstrap else EventKind.AGENT_HIRED
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "role": role.value,
            "manager_id": reports_to,
            "first_name": first_name,
            "last_name": last_name,
            "role_title": role_title,
        }
        if not bootstrap:
            payload["by_agent_id"] = requested_by
        self._events.append(kind, payload, actor=requested_by)

        return Agent(
            id=agent_id,
            role=role,
            persona_ref=persona_ref,
            reports_to=reports_to,
            status=Status.ACTIVE,
            hired_at=now,
            first_name=first_name,
            last_name=last_name,
            role_title=role_title,
            role_description=role_description,
        )

    def get(self, agent_id: str) -> Agent:
        row = self._db.connect().execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            raise KeyError(agent_id)
        return self._row_to_agent(row)

    def all(self, status: Status | None = None) -> list[Agent]:
        if status is None:
            rows = self._db.connect().execute(
                "SELECT * FROM agents ORDER BY hired_at"
            ).fetchall()
        else:
            rows = self._db.connect().execute(
                "SELECT * FROM agents WHERE status = ? ORDER BY hired_at",
                (status.value,),
            ).fetchall()
        return [self._row_to_agent(r) for r in rows]

    def active_count(self) -> int:
        row = self._db.connect().execute(
            "SELECT COUNT(*) AS cnt FROM agents WHERE status = ?",
            (Status.ACTIVE.value,),
        ).fetchone()
        return int(row["cnt"])

    def has_role(self, role: Role, status: Status = Status.ACTIVE) -> bool:
        row = self._db.connect().execute(
            "SELECT 1 FROM agents WHERE role = ? AND status = ? LIMIT 1",
            (role.value, status.value),
        ).fetchone()
        return row is not None

    def is_special(self, agent_id: str) -> bool:
        a = self.get(agent_id)
        return a.role in SPECIAL_ROLES

    def update_role_details(
        self,
        *,
        target_id: str,
        role_title: str,
        role_description: str,
    ) -> Agent:
        """Update the flexible role title/description for an active agent.

        The coarse RBAC role remains unchanged; this affects the agent's
        job definition and persona context.
        """
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM agents WHERE id = ? AND status = ?",
                (target_id, Status.ACTIVE.value),
            ).fetchone()
            if row is None:
                raise KeyError(target_id)
            conn.execute(
                "UPDATE agents SET role_title = ?, role_description = ? "
                "WHERE id = ?",
                (role_title, role_description, target_id),
            )
            refreshed = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (target_id,),
            ).fetchone()
        return self._row_to_agent(refreshed)

    def reassign(
        self,
        *,
        actor_id: str,
        target_id: str,
        new_reports_to: str,
        reason: str,
    ) -> Agent:
        """Move ``target_id`` under ``new_reports_to``.

        Used to rebalance the org chart when a new builder-side manager
        is hired but the CEO is already at the direct-report cap. The
        actor must be either the CEO or the target's current manager.

        Same invariants as :meth:`add_agent` apply to the new edge:
        - target and new manager must be active
        - no self-report, no cycle (new manager cannot be a descendant)
        - new manager must have capacity (CEO ≤5, others ≤4)
        - HR can only manage HR agents
        - the three special roles (CEO/HR/Security) cannot be moved —
          their position in the chart is fixed by the bootstrap.
        """
        cleaned_reason = " ".join(reason.split())
        if len(cleaned_reason) < _MIN_FIRE_REASON_CHARS:
            raise FireDenied(
                f"reassign_reason_too_short:{_MIN_FIRE_REASON_CHARS}"
            )
        if target_id == new_reports_to:
            raise CycleDetected("self_report")

        with self._db.transaction() as conn:
            target_row = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (target_id,),
            ).fetchone()
            if target_row is None:
                raise FireDenied(f"target_not_found:{target_id}")
            if target_row["status"] != Status.ACTIVE.value:
                raise FireDenied(f"target_not_active:{target_id}")
            target_role = Role(target_row["role"])
            if target_role in SPECIAL_ROLES:
                raise SpecialRoleProtected(
                    f"cannot_reassign_special_role:{target_role.value}"
                )

            new_mgr_row = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (new_reports_to,),
            ).fetchone()
            if new_mgr_row is None:
                raise FireDenied(f"manager_not_found:{new_reports_to}")
            if new_mgr_row["status"] != Status.ACTIVE.value:
                raise FireDenied(f"manager_not_active:{new_reports_to}")
            new_mgr_role = Role(new_mgr_row["role"])

            current_manager_id: str | None = target_row["reports_to"]
            if current_manager_id == new_reports_to:
                # Already there — no-op return rather than a misleading event.
                return self._row_to_agent(target_row)

            actor_row = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (actor_id,),
            ).fetchone()
            if actor_row is None or actor_row["status"] != Status.ACTIVE.value:
                raise FireDenied(f"actor_not_active:{actor_id}")
            actor_role = Role(actor_row["role"])
            if actor_role is not Role.CEO and actor_id != current_manager_id:
                raise FireDenied("only_current_manager_or_ceo_may_reassign")

            # HR's direct reports must be HR (mirrors add_agent).
            if new_mgr_role is Role.HR and target_role is not Role.HR:
                raise HireDenied(f"hr_can_only_manage_hr:{new_reports_to}")

            # Span-of-control on the destination manager.
            limit = _direct_report_limit(new_mgr_role)
            count_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM agents "
                "WHERE reports_to = ? AND status = ?",
                (new_reports_to, Status.ACTIVE.value),
            ).fetchone()
            if count_row["cnt"] >= limit:
                raise HireDenied(
                    f"direct_report_limit_exceeded:{new_reports_to}:{limit}"
                )

            # Cycle prevention: new manager must not be a descendant of
            # the target. Walk new_mgr's chain up; if we hit target_id
            # before the root, the proposed edge would close a loop.
            cur = new_mgr_row["reports_to"]
            while cur is not None:
                if cur == target_id:
                    raise CycleDetected(
                        f"cycle:{target_id}->{new_reports_to}"
                    )
                step = conn.execute(
                    "SELECT reports_to FROM agents WHERE id = ?", (cur,),
                ).fetchone()
                if step is None:
                    break
                cur = step["reports_to"]

            conn.execute(
                "UPDATE agents SET reports_to = ? WHERE id = ?",
                (new_reports_to, target_id),
            )
            refreshed = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (target_id,),
            ).fetchone()

        self._events.append(
            EventKind.AGENT_REASSIGNED,
            {
                "agent_id": target_id,
                "from_manager_id": current_manager_id,
                "to_manager_id": new_reports_to,
                "by_agent_id": actor_id,
                "reason": cleaned_reason,
            },
            actor=actor_id,
        )
        return self._row_to_agent(refreshed)

    # ------------------------------------------------------------- org graph

    def manager_of(self, agent_id: str) -> str | None:
        row = self._db.connect().execute(
            "SELECT reports_to FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            raise KeyError(agent_id)
        result: str | None = row["reports_to"]
        return result

    def direct_report_count(self, manager_id: str) -> int:
        row = self._db.connect().execute(
            "SELECT COUNT(*) AS cnt FROM agents "
            "WHERE reports_to = ? AND status = ?",
            (manager_id, Status.ACTIVE.value),
        ).fetchone()
        return int(row["cnt"])

    def direct_reports(self, agent_id: str) -> list[str]:
        rows = self._db.connect().execute(
            "SELECT id FROM agents "
            "WHERE reports_to = ? AND status = ? ORDER BY hired_at",
            (agent_id, Status.ACTIVE.value),
        ).fetchall()
        return [r["id"] for r in rows]

    def all_descendants(self, agent_id: str) -> list[str]:
        out: list[str] = []
        frontier = list(self.direct_reports(agent_id))
        while frontier:
            current = frontier.pop()
            out.append(current)
            frontier.extend(self.direct_reports(current))
        return out

    def chain_up(self, agent_id: str) -> list[str]:
        """Manager chain from immediate manager up to the top (exclusive of self)."""
        chain: list[str] = []
        seen: set[str] = {agent_id}
        cur = self.manager_of(agent_id)
        while cur is not None:
            if cur in seen:  # defensive — DB invariant prevents cycles
                raise CycleDetected(f"cycle_at:{cur}")
            seen.add(cur)
            chain.append(cur)
            cur = self.manager_of(cur)
        return chain

    def depth(self, ancestor_id: str, descendant_id: str) -> int | None:
        """Levels between ``ancestor`` and ``descendant``.

        Returns ``1`` for a direct report, ``2`` for a grand-report, etc.
        ``None`` if ancestor is not actually an ancestor (or equal).
        """
        if ancestor_id == descendant_id:
            return 0
        d = 0
        cur = self.manager_of(descendant_id)
        while cur is not None:
            d += 1
            if cur == ancestor_id:
                return d
            cur = self.manager_of(cur)
        return None

    # --------------------------------------------------------------- fire

    def fire(
        self,
        *,
        actor_id: str,
        target_id: str,
        reason: str,
        via_hr: bool,
    ) -> FireOutcome:
        """Authorise + execute (or queue) a fire request.

        See PLAN.md fire algorithm section for the full decision table.
        """
        if not via_hr:
            return FireOutcome(FireResult.DENIED, "must_route_via_hr")

        actor = self._get_active(actor_id)
        target = self._get_active(target_id)
        if actor is None:
            return FireOutcome(FireResult.DENIED, "actor_not_active")
        if target is None:
            return FireOutcome(FireResult.DENIED, "target_not_active")

        if actor.id == target.id:
            return FireOutcome(FireResult.DENIED, "self_fire")

        # Special role guards on the *target*.
        if target.role is Role.CEO:
            return FireOutcome(FireResult.DENIED, "ceo_cannot_be_fired_directly")
        if target.role is Role.HR and not self._has_other_active_hr(target.id):
            return FireOutcome(FireResult.DENIED, "no_hr_replacement")

        d = self.depth(actor.id, target.id)
        if d is None or d <= 0:
            return FireOutcome(FireResult.DENIED, "not_subordinate")

        reason_error = _validate_fire_reason(reason)
        if reason_error is not None:
            return FireOutcome(FireResult.DENIED, reason_error)

        # Any subordinate can now be fired immediately by an authorised manager.
        # For indirect reports, notify the direct manager so they can re-plan.
        self._do_fire(
            actor_id=actor.id,
            target_id=target.id,
            reason=reason,
            notify_manager=True,
        )
        return FireOutcome(FireResult.DIRECT_FIRED)

    def complete_fire(
        self, request_id: str, decision: ApprovalDecision
    ) -> None:
        """Approval callback. Approves or cancels a queued fire request."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM fire_pending WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            conn.execute(
                "DELETE FROM fire_pending WHERE request_id = ?", (request_id,)
            )

        if decision is ApprovalDecision.DENIED:
            return

        # APPROVED → execute the fire. Notify the immediate manager only when
        # someone else than the actor would be informed.
        self._do_fire(
            actor_id=row["actor_id"],
            target_id=row["target_id"],
            reason=row["reason"],
            notify_manager=True,
        )

    # ----------------------------------------------------- workspace ACL

    def can_read_workspace(self, reader_id: str, owner_id: str) -> bool:
        if reader_id == owner_id:
            return True
        try:
            return self.manager_of(owner_id) == reader_id
        except KeyError:
            return False

    # ---------------------------------------------------------- internals

    def _get_active(self, agent_id: str) -> Agent | None:
        row = self._db.connect().execute(
            "SELECT * FROM agents WHERE id = ? AND status = ?",
            (agent_id, Status.ACTIVE.value),
        ).fetchone()
        return None if row is None else self._row_to_agent(row)

    def _has_other_active_hr(self, exclude_id: str) -> bool:
        row = self._db.connect().execute(
            "SELECT 1 FROM agents "
            "WHERE role = ? AND status = ? AND id != ? LIMIT 1",
            (Role.HR.value, Status.ACTIVE.value, exclude_id),
        ).fetchone()
        return row is not None

    def _enqueue_pending_fire(
        self, actor_id: str, target_id: str, reason: str, decider: str
    ) -> FireOutcome:
        # Idempotent on target_id: if a request already exists, return it.
        existing = self._db.connect().execute(
            "SELECT request_id FROM fire_pending WHERE target_id = ?",
            (target_id,),
        ).fetchone()
        if existing is not None:
            return FireOutcome(
                FireResult.PENDING_APPROVAL,
                request_id=existing["request_id"],
            )

        request_id = _new_id()
        now = _utcnow().isoformat()
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO fire_pending "
                    "(request_id, actor_id, target_id, reason, decider_id, "
                    " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (request_id, actor_id, target_id, reason, decider, now),
                )
        except sqlite3.IntegrityError:
            existing = self._db.connect().execute(
                "SELECT request_id FROM fire_pending WHERE target_id = ?",
                (target_id,),
            ).fetchone()
            if existing is not None:
                return FireOutcome(
                    FireResult.PENDING_APPROVAL,
                    request_id=existing["request_id"],
                )
            raise

        self._approvals.request_fire(
            request_id=request_id,
            actor_id=actor_id,
            target_id=target_id,
            decider_id=decider,
            reason=reason,
        )
        return FireOutcome(FireResult.PENDING_APPROVAL, request_id=request_id)

    def _do_fire(
        self,
        *,
        actor_id: str,
        target_id: str,
        reason: str,
        notify_manager: bool,
    ) -> None:
        """Mark target fired, orphan its reports, emit the event suite."""
        now = _utcnow()
        former_manager = self.manager_of(target_id)
        orphans = self.direct_reports(target_id)

        # Last-chance guard: HR replacement might have changed since the
        # fire was queued. Re-check inside the transaction window.
        target = self.get(target_id)
        if target.role is Role.HR and not self._has_other_active_hr(target_id):
            raise SpecialRoleProtected("no_hr_replacement")

        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET status = ?, fired_at = ?, fired_by = ?, "
                "fire_reason = ? WHERE id = ?",
                (
                    Status.FIRED.value,
                    now.isoformat(),
                    actor_id,
                    reason,
                    target_id,
                ),
            )
            # Detach any direct reports (they become orphans). The CEO/HR
            # are expected to re-assign them.
            conn.execute(
                "UPDATE agents SET reports_to = NULL "
                "WHERE reports_to = ? AND status = ?",
                (target_id, Status.ACTIVE.value),
            )

        self._events.append(
            EventKind.AGENT_FIRED,
            {"agent_id": target_id, "by_agent_id": actor_id, "reason": reason},
            actor=actor_id,
        )

        if notify_manager and former_manager and former_manager != actor_id:
            self._events.append(
                EventKind.AGENT_FIRE_NOTIFY,
                {
                    "to_agent": former_manager,
                    "fired_agent": target_id,
                    "actor": actor_id,
                    "reason": reason,
                },
                actor=actor_id,
            )

        for orphan_id in orphans:
            self._events.append(
                EventKind.AGENT_ORPHANED,
                {"agent_id": orphan_id, "former_manager_id": target_id},
                actor=actor_id,
            )

    def _row_to_agent(self, row: sqlite3.Row) -> Agent:
        hired = _parse_dt(row["hired_at"])
        assert hired is not None
        keys = row.keys()
        return Agent(
            id=row["id"],
            role=Role(row["role"]),
            persona_ref=row["persona_ref"],
            reports_to=row["reports_to"],
            status=Status(row["status"]),
            hired_at=hired,
            first_name=row["first_name"] if "first_name" in keys else "",
            last_name=row["last_name"] if "last_name" in keys else "",
            role_title=row["role_title"] if "role_title" in keys else None,
            role_description=row["role_description"] if "role_description" in keys else None,
            fired_at=_parse_dt(row["fired_at"]),
            fired_by=row["fired_by"],
            fire_reason=row["fire_reason"],
        )
