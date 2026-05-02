"""Performance reporting and manager actions."""
from __future__ import annotations

import contextlib
import json
import statistics
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.approvals import Approval, ApprovalKind, ApprovalStatus
from modules.event_store import Event, EventKind, EventStore
from modules.identity import ApprovalDecision, FireResult, Org, Role, Status
from modules.storage import CompanyDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .models import (
    Feedback,
    FireRequestOutcome,
    PerformanceMetrics,
    PerformanceReport,
    RoleChangeProposal,
)

_MODULE_KEY = "performance"
_READ_BATCH_SIZE = 1000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Performance:
    """Per-company performance service.

    Metrics are derived from the append-only event log on read. Manager
    feedback is persisted because it is explicit business state rather than
    an inferred signal.
    """

    def __init__(
        self,
        db: CompanyDB,
        events: EventStore,
        identity: Org,
        approvals: Any,
        *,
        now: Any | None = None,
    ) -> None:
        self._db = db
        self._events = events
        self._identity = identity
        self._approvals = approvals
        self._now = now or _utcnow

    # --------------------------------------------------------------- migrate

    def migrate(self) -> None:
        apply_migrations(
            self._db.connect(),
            module=_MODULE_KEY,
            migrations=load_migrations_from_package("modules.performance.migrations"),
        )

    # --------------------------------------------------------------- reporting

    def report(self, agent_id: str, *, feedback_limit: int = 10) -> PerformanceReport:
        target = self._identity.get(agent_id)
        events = self._read_all_events()
        manager_id = target.reports_to

        messages_sent = 0
        messages_received = 0
        manager_messages: list[Event] = []
        replies_to_manager: list[Event] = []
        tool_called = 0
        tool_ok = 0
        tasks_completed = 0
        truncated_turns = 0
        health_alerts = 0

        for event in events:
            payload = event.payload
            if event.kind is EventKind.MESSAGE_SENT:
                from_agent = payload.get("from_agent")
                to_agent = payload.get("to_agent")
                if from_agent == agent_id:
                    messages_sent += 1
                if to_agent == agent_id:
                    messages_received += 1
                if manager_id and from_agent == manager_id and to_agent == agent_id:
                    manager_messages.append(event)
                if manager_id and from_agent == agent_id and to_agent == manager_id:
                    replies_to_manager.append(event)
            elif event.kind is EventKind.TOOL_CALLED:
                if event.actor_agent_id == agent_id:
                    tool_called += 1
            elif event.kind is EventKind.TOOL_RESULT:
                if event.actor_agent_id == agent_id and payload.get("ok") is True:
                    tool_ok += 1
            elif event.kind is EventKind.AGENT_TURN_COMPLETED:
                if payload.get("agent_id") == agent_id and payload.get("task_done") is True:
                    tasks_completed += 1
            elif event.kind is EventKind.AGENT_TURN_TRUNCATED:
                if payload.get("agent_id") == agent_id:
                    truncated_turns += 1
            elif event.kind is EventKind.AGENT_HEALTH_ALERT:
                if payload.get("agent_id") == agent_id:
                    health_alerts += 1

        tool_success_rate = (
            (tool_ok / tool_called) if tool_called > 0 else None
        )
        avg_response_seconds = _median_response_seconds(
            manager_messages,
            replies_to_manager,
        )
        tenure_days = max(0, (self._now() - target.hired_at).days)

        return PerformanceReport(
            metrics=PerformanceMetrics(
                tasks_completed=tasks_completed,
                messages_sent=messages_sent,
                messages_received=messages_received,
                tool_success_rate=tool_success_rate,
                avg_response_seconds=avg_response_seconds,
                truncated_turns=truncated_turns,
                health_alerts=health_alerts,
                tenure_days=tenure_days,
            ),
            recent_feedback=self.recent_feedback(agent_id, limit=feedback_limit),
        )

    def recent_feedback(self, agent_id: str, *, limit: int = 10) -> list[Feedback]:
        rows = self._db.connect().execute(
            "SELECT * FROM feedback WHERE target_agent_id = ? "
            "ORDER BY ts DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        return [_row_to_feedback(row) for row in rows]

    async def send_report_to_manager(
        self,
        *,
        target_id: str,
        orchestrator: Any,
    ) -> str:
        target = self._identity.get(target_id)
        manager_id = target.reports_to
        if manager_id is None:
            raise PermissionError("target_has_no_manager")
        report = self.report(target_id)
        body = _format_manager_report(target, report)
        result = await orchestrator.system_send(manager_id, body)
        if getattr(result, "value", str(result)) != "queued":
            raise RuntimeError(f"report_delivery_failed:{result}")
        return manager_id

    # --------------------------------------------------------------- feedback

    def give_feedback(
        self,
        *,
        caller_id: str,
        target_id: str,
        rating: int,
        note: str,
    ) -> Feedback:
        if rating < 1 or rating > 5:
            raise ValueError("rating_out_of_range")
        self._require_feedback_authority(caller_id, target_id)
        feedback_id = _new_id()
        now = self._now()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO feedback "
                "(id, target_agent_id, from_agent_id, rating, note, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    feedback_id,
                    target_id,
                    caller_id,
                    rating,
                    note,
                    now.isoformat(),
                ),
            )
        self._events.append(
            EventKind.AGENT_FEEDBACK_GIVEN,
            {
                "feedback_id": feedback_id,
                "target_agent_id": target_id,
                "from_agent_id": caller_id,
                "rating": rating,
            },
            actor=caller_id,
        )
        return Feedback(
            id=feedback_id,
            target_agent_id=target_id,
            from_agent_id=caller_id,
            rating=rating,
            note=note,
            ts=now,
        )

    # ------------------------------------------------------------ role change

    def propose_role_change(
        self,
        *,
        caller_id: str,
        target_id: str,
        new_role_title: str,
        new_role_description: str,
        rationale: str,
    ) -> RoleChangeProposal:
        self._require_direct_manager(caller_id, target_id)
        self._apply_role_change(
            caller_id=caller_id,
            target_id=target_id,
            new_role_title=new_role_title,
            new_role_description=new_role_description,
            rationale=rationale,
        )
        return RoleChangeProposal(status="updated")

    def complete_role_change(self, approval: Approval) -> None:
        if approval.kind is not ApprovalKind.ROLE_CHANGE:
            return
        if approval.status is not ApprovalStatus.APPROVED:
            return
        payload = approval.payload
        target_id = str(payload.get("target_agent_id") or "")
        actor_id = str(payload.get("from_agent_id") or approval.requester_id)
        new_title = str(payload.get("new_role_title") or "").strip()
        new_description = str(payload.get("new_role_description") or "").strip()
        if not target_id or not new_title:
            raise ValueError("invalid_role_change_payload")

        self._apply_role_change(
            caller_id=actor_id,
            target_id=target_id,
            new_role_title=new_title,
            new_role_description=new_description,
            rationale=str(payload.get("rationale") or ""),
            approval_id=approval.request_id,
        )

    # --------------------------------------------------------------- fire

    def propose_fire(
        self,
        *,
        caller_id: str,
        target_id: str,
        reason: str,
    ) -> FireRequestOutcome:
        outcome = self._identity.fire(
            actor_id=caller_id,
            target_id=target_id,
            reason=reason,
            via_hr=True,
        )
        return FireRequestOutcome(
            result=outcome.result.value,
            reason=outcome.reason,
            request_id=outcome.request_id,
        )

    # -------------------------------------------------------------- approvals

    def apply_approval_side_effect(self, approval: Approval) -> None:
        if approval.kind is ApprovalKind.FIRE_DEPTH_1:
            decision = (
                ApprovalDecision.APPROVED
                if approval.status is ApprovalStatus.APPROVED
                else ApprovalDecision.DENIED
            )
            if approval.status in (ApprovalStatus.APPROVED, ApprovalStatus.DENIED):
                self._identity.complete_fire(approval.request_id, decision)
        elif approval.kind is ApprovalKind.ROLE_CHANGE:
            self.complete_role_change(approval)

    # --------------------------------------------------------------- helpers

    def _read_all_events(self) -> list[Event]:
        out: list[Event] = []
        since = 0
        while True:
            batch = self._events.read(since=since, limit=_READ_BATCH_SIZE)
            if not batch:
                break
            out.extend(batch)
            since = batch[-1].id
            if len(batch) < _READ_BATCH_SIZE:
                break
        return out

    def _require_feedback_authority(self, caller_id: str, target_id: str) -> None:
        caller = self._identity.get(caller_id)
        target = self._identity.get(target_id)
        if caller.status is not Status.ACTIVE or target.status is not Status.ACTIVE:
            raise PermissionError("agent_not_active")
        if target.reports_to == caller_id:
            return
        if caller.role is Role.CEO:
            return
        raise PermissionError("not_manager")

    def _require_direct_manager(self, caller_id: str, target_id: str) -> None:
        caller = self._identity.get(caller_id)
        target = self._identity.get(target_id)
        if caller.status is not Status.ACTIVE or target.status is not Status.ACTIVE:
            raise PermissionError("agent_not_active")
        if target.reports_to != caller_id:
            raise PermissionError("not_direct_manager")

    def _apply_role_change(
        self,
        *,
        caller_id: str,
        target_id: str,
        new_role_title: str,
        new_role_description: str,
        rationale: str,
        approval_id: str | None = None,
    ) -> None:
        new_title = new_role_title.strip()
        new_description = new_role_description.strip()
        if not new_title:
            raise ValueError("invalid_role_change_payload")
        before = self._identity.get(target_id)
        updated = self._identity.update_role_details(
            target_id=target_id,
            role_title=new_title,
            role_description=new_description,
        )
        payload: dict[str, Any] = {
            "agent_id": target_id,
            "by_agent_id": caller_id,
            "old_role_title": _display_role_title(before),
            "new_role_title": _display_role_title(updated),
            "old_role_description": before.role_description,
            "new_role_description": updated.role_description or "",
        }
        if approval_id is not None:
            payload["approval_id"] = approval_id
        self._events.append(EventKind.AGENT_PROMOTED, payload, actor=caller_id)
        self._cancel_pending_role_change_approvals(
            target_id=target_id,
            by=caller_id,
            except_request_id=approval_id,
        )
        self._notify_hr_role_change(
            caller_id=caller_id,
            target_id=target_id,
            new_role_title=new_title,
            rationale=rationale,
        )

    def _cancel_pending_role_change_approvals(
        self,
        *,
        target_id: str,
        by: str,
        except_request_id: str | None = None,
    ) -> None:
        rows = self._db.connect().execute(
            "SELECT request_id, payload_json FROM approvals "
            "WHERE kind = 'role_change' AND status = 'pending'"
        ).fetchall()
        for row in rows:
            request_id = row["request_id"]
            if except_request_id and request_id == except_request_id:
                continue
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if payload.get("target_agent_id") != target_id:
                continue
            with contextlib.suppress(Exception):
                self._approvals.cancel(
                    request_id,
                    by=by,
                    reason="role_change_applied_directly",
                )

    def _notify_hr_role_change(
        self,
        *,
        caller_id: str,
        target_id: str,
        new_role_title: str,
        rationale: str,
    ) -> None:
        for agent in self._identity.all(status=Status.ACTIVE):
            if agent.role is Role.HR:
                self._events.append(
                    EventKind.MESSAGE_SENT,
                    {
                        "from_agent": "system",
                        "to_agent": agent.id,
                        "content": (
                            "Role change applied. "
                            f"Target: {target_id}. "
                            f"Requested by: {caller_id}. "
                            f"New title: {new_role_title}. "
                            f"Rationale: {rationale}"
                        ),
                    },
                    actor=caller_id,
                )


def _median_response_seconds(
    manager_messages: list[Event],
    replies_to_manager: list[Event],
) -> float | None:
    if not manager_messages or not replies_to_manager:
        return None

    used_reply_ids: set[int] = set()
    deltas: list[float] = []
    for msg in manager_messages:
        thread_id = msg.payload.get("thread_id")
        candidates = [
            reply
            for reply in replies_to_manager
            if reply.id not in used_reply_ids
            and reply.ts_company >= msg.ts_company
            and (
                not thread_id
                or reply.payload.get("thread_id") == thread_id
                or reply.correlation_id == msg.correlation_id
            )
        ]
        if not candidates:
            candidates = [
                reply
                for reply in replies_to_manager
                if reply.id not in used_reply_ids
                and reply.ts_company >= msg.ts_company
            ]
        if not candidates:
            continue
        reply = min(candidates, key=lambda e: e.ts_company)
        used_reply_ids.add(reply.id)
        deltas.append((reply.ts_company - msg.ts_company).total_seconds())
    if not deltas:
        return None
    return float(statistics.median(deltas))


def _row_to_feedback(row: Any) -> Feedback:
    return Feedback(
        id=row["id"],
        target_agent_id=row["target_agent_id"],
        from_agent_id=row["from_agent_id"],
        rating=int(row["rating"]),
        note=row["note"],
        ts=_parse_dt(row["ts"]),
    )


def _display_role_title(agent: Any) -> str:
    return agent.role_title or agent.role.value


def _format_manager_report(target: Any, report: PerformanceReport) -> str:
    metrics = report.metrics
    title = _display_role_title(target)
    success = (
        f"{metrics.tool_success_rate:.0%}"
        if metrics.tool_success_rate is not None
        else "n/a"
    )
    response = (
        f"{metrics.avg_response_seconds:.1f}s"
        if metrics.avg_response_seconds is not None
        else "n/a"
    )
    lines = [
        f"Performance report for {target.id} ({title})",
        f"- tasks_completed: {metrics.tasks_completed}",
        f"- messages_sent / received: {metrics.messages_sent} / {metrics.messages_received}",
        f"- tool_success_rate: {success}",
        f"- median manager-response time: {response}",
        f"- truncated_turns: {metrics.truncated_turns}",
        f"- health_alerts: {metrics.health_alerts}",
        f"- tenure_days: {metrics.tenure_days}",
    ]
    if report.recent_feedback:
        lines.append("- recent_feedback:")
        for item in report.recent_feedback[:3]:
            lines.append(f"  {item.rating}/5 from {item.from_agent_id}: {item.note[:160]}")
    lines.append(
        "You can use give_feedback, propose_role_change, or propose_fire if action is needed."
    )
    return "\n".join(lines)
