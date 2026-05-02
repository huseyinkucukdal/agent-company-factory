"""High-level Security Policy entry points.

This module wires together :mod:`static_rules` and the surrounding company
services (Approvals, Event Store, Identity, Orchestrator) to produce three
operations described in the PLAN:

* :meth:`SecurityPolicy.review_approval`  — verdict on a pending approval
  whose route requires a security pre-veto.
* :meth:`SecurityPolicy.scan_recent_events` — proactive sweep of recent
  events; emits findings (does not block).
* :meth:`SecurityPolicy.auto_flag` — turn a finding into a real-world
  action: :class:`SECURITY_FLAG` approval and (for CRITICAL findings) an
  immediate orchestrator suspension.

All collaborators are passed via :class:`SecurityDeps`; tests construct
fakes implementing the structural protocols.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from modules.approvals import (
    Approval,
    ApprovalKind,
    ApprovalRoute,
    Approvals,
    Decision,
    RouteTarget,
)
from modules.event_store import Event, EventKind, EventStore

from .models import ReviewOutcome, ReviewResult, SecurityFinding, Severity
from .static_rules import RuleContext, StaticRuleSet, default_context


class _Suspender(Protocol):
    def suspend(self, agent_id: str, *, reason: str = ...) -> None: ...


# Optional async callable that takes the approval + static findings and
# returns a final Decision. Used to delegate ambiguous cases to an LLM.
LLMReviewer = Callable[
    [Approval, list[SecurityFinding]],
    Awaitable[tuple[Decision, str]],
]


@dataclass
class SecurityDeps:
    approvals: Approvals
    events: EventStore
    orchestrator: _Suspender | None = None
    rules: StaticRuleSet = field(default_factory=StaticRuleSet)
    rule_context_factory: Callable[[], RuleContext] = field(
        default=default_context,
    )
    llm_reviewer: LLMReviewer | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now())


class SecurityPolicy:
    """Per-company security agent runtime.

    `agent_id` identifies the security agent in audit logs. It is used
    as ``decider_id`` when recording security pre-veto decisions and as
    ``actor`` for emitted events.
    """

    def __init__(self, agent_id: str, deps: SecurityDeps) -> None:
        self._agent_id = agent_id
        self._deps = deps

    # --------------------------------------------------------- review

    async def review_approval(self, approval: Approval) -> ReviewResult:
        """Run static rules and (optionally) LLM review on `approval`.

        Side effects:
        * On hard DENY: records security pre-veto via ``Approvals.decide``
          and emits a ``SECURITY_VETO`` event.
        * On hard APPROVE (route requires security): records the approval
          via ``Approvals.decide``.
        * On DEFERRED: no decision is recorded — caller must follow up.
        """
        ctx = self._deps.rule_context_factory()
        findings = self._deps.rules.evaluate(approval, ctx)

        max_sev = _max_severity(findings)
        if max_sev is Severity.CRITICAL:
            self._record_decision(approval, Decision.DENY, _findings_note(findings))
            self._emit_veto(approval, findings)
            return ReviewResult(
                outcome=ReviewOutcome.DENIED,
                findings=findings,
                reason="critical_finding",
            )

        if max_sev is Severity.HIGH:
            if self._deps.llm_reviewer is None:
                return ReviewResult(
                    outcome=ReviewOutcome.DEFERRED,
                    findings=findings,
                    reason="needs_human_review",
                )
            decision, note = await self._deps.llm_reviewer(approval, findings)
            self._record_decision(approval, decision, note)
            if decision is Decision.DENY:
                self._emit_veto(approval, findings, note=note)
                return ReviewResult(
                    outcome=ReviewOutcome.DENIED,
                    findings=findings,
                    reason=f"llm_denied:{note}",
                )
            return ReviewResult(
                outcome=ReviewOutcome.APPROVED,
                findings=findings,
                reason=f"llm_approved:{note}",
            )

        # INFO / WARN / no findings — auto-approve the security leg.
        if approval.route.require_security:
            self._record_decision(approval, Decision.APPROVE, "auto_clear")
        return ReviewResult(
            outcome=ReviewOutcome.APPROVED,
            findings=findings,
            reason="auto_clear",
        )

    # --------------------------------------------------------- scan

    async def scan_recent_events(
        self, *, window_minutes: int = 5,
    ) -> list[SecurityFinding]:
        """Sweep the recent event window and return any findings."""
        cutoff = self._deps.now() - timedelta(minutes=window_minutes)
        events: list[Event] = [
            e for e in self._deps.events.read(
                kinds=[
                    EventKind.EXTERNAL_CALL,
                    EventKind.EXPENSE_CHARGED,
                    EventKind.AGENT_HEALTH_ALERT,
                ],
                limit=1000,
            )
            if e.ts_company >= cutoff
        ]
        ctx = self._deps.rule_context_factory()
        return self._deps.rules.evaluate_events(events, ctx)

    # --------------------------------------------------------- act

    async def auto_flag(self, finding: SecurityFinding) -> Approval | None:
        """Translate a finding into a SECURITY_FLAG approval + side effects.

        * INFO findings are dropped (caller may log them).
        * WARN/HIGH/CRITICAL findings emit a ``SECURITY_FLAG`` event and
          create a ``SECURITY_FLAG`` approval routed to the Board.
        * CRITICAL findings additionally trigger orchestrator suspension
          of the offending agent (if known and a suspender is wired in).
        """
        if finding.severity is Severity.INFO:
            return None

        target = finding.subject_agent or "unknown"
        self._deps.events.append(
            EventKind.SECURITY_FLAG,
            {
                "severity": finding.severity.value,
                "target": target,
                "rule": finding.rule,
                "note": finding.description,
            },
            actor=self._agent_id,
        )

        if (
            finding.severity is Severity.CRITICAL
            and self._deps.orchestrator is not None
            and finding.subject_agent
        ):
            self._deps.orchestrator.suspend(
                finding.subject_agent,
                reason=f"security:{finding.rule}",
            )

        return self._deps.approvals.request(
            kind=ApprovalKind.SECURITY_FLAG,
            requester_id=self._agent_id,
            payload={
                "severity": finding.severity.value,
                "rule": finding.rule,
                "subject_agent": target,
                "description": finding.description,
                "evidence": finding.evidence,
                "suggested_action": finding.suggested_action,
            },
            route=ApprovalRoute(target=RouteTarget.BOARD),
        )

    # --------------------------------------------------------- internals

    def _record_decision(
        self, approval: Approval, decision: Decision, note: str,
    ) -> None:
        if not approval.route.require_security:
            return
        self._deps.approvals.decide(
            approval.request_id,
            self._agent_id,
            decision,
            note=note,
        )

    def _emit_veto(
        self, approval: Approval, findings: list[SecurityFinding],
        *, note: str | None = None,
    ) -> None:
        rule = findings[0].rule if findings else "unspecified"
        self._deps.events.append(
            EventKind.SECURITY_VETO,
            {
                "target": approval.requester_id,
                "rule": rule,
                "note": note or _findings_note(findings),
            },
            actor=self._agent_id,
        )


# ------------------------------------------------------------- helpers


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.WARN: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


def _max_severity(findings: list[SecurityFinding]) -> Severity | None:
    if not findings:
        return None
    return max(findings, key=lambda f: _SEVERITY_ORDER[f.severity]).severity


def _findings_note(findings: list[SecurityFinding]) -> str:
    if not findings:
        return "no_findings"
    parts: list[str] = []
    for f in findings[:3]:
        parts.append(f"{f.severity.value}:{f.rule}")
    return "; ".join(parts)


__all__: list[str] = [
    "LLMReviewer",
    "SecurityDeps",
    "SecurityPolicy",
]
