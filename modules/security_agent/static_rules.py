"""Static rule engine for Security Agent.

A *static rule* is a pure function that inspects an :class:`Approval` (or a
window of :class:`Event` objects for proactive scanning) and returns zero
or more :class:`SecurityFinding` objects. Rules are deterministic and
side-effect free — they never block, never allocate IO and never call
LLMs. All ambiguity is delegated to the LLM-backed reviewer hook.

Rules are registered via :class:`StaticRuleSet`. Tests can build an empty
set, register only the rules under test, and run :meth:`evaluate`.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from modules.approvals import Approval, ApprovalKind
from modules.event_store import Event, EventKind

from .models import SecurityFinding, Severity

# Rule takes an approval and returns 0..N findings.
ApprovalRule = Callable[[Approval, "RuleContext"], list[SecurityFinding]]
EventRule = Callable[[list[Event], "RuleContext"], list[SecurityFinding]]


@dataclass
class RuleContext:
    """Shared inputs every rule may consult."""

    allowlist_check: Callable[[str, str], bool] = lambda _s, _a: True
    budget_total_cents: int = 0
    pii_patterns: list[re.Pattern[str]] = field(default_factory=list)
    high_volume_threshold_per_minute: int = 10
    retry_threshold_per_minute: int = 5
    workspace_root: str = "/data/companies"


_DEFAULT_PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                  # SSN
    re.compile(r"\b(?:\d[ -]*?){13,16}\b"),                # credit-card-ish
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),       # IBAN-ish
]


def default_context(
    *, allowlist_check: Callable[[str, str], bool] | None = None,
    budget_total_cents: int = 0,
) -> RuleContext:
    return RuleContext(
        allowlist_check=allowlist_check or (lambda _s, _a: True),
        budget_total_cents=budget_total_cents,
        pii_patterns=list(_DEFAULT_PII_PATTERNS),
    )


# ------------------------------------------------------------- rule library


def rule_external_call_allowlist(
    approval: Approval, ctx: RuleContext,
) -> list[SecurityFinding]:
    if approval.kind is not ApprovalKind.EXTERNAL_ACTION:
        return []
    service = str(approval.payload.get("service", ""))
    action = str(approval.payload.get("action", ""))
    if not service:
        return []
    if not ctx.allowlist_check(service, action):
        return [SecurityFinding(
            severity=Severity.CRITICAL,
            rule="external_call_allowlist",
            description=(
                f"External call to {service}/{action} is not on the "
                f"allowlist."
            ),
            evidence={"service": service, "action": action},
            suggested_action="deny",
            subject_agent=approval.requester_id,
        )]
    return []


def rule_expense_threshold(
    approval: Approval, ctx: RuleContext,
) -> list[SecurityFinding]:
    if approval.kind is not ApprovalKind.EXPENSE:
        return []
    if ctx.budget_total_cents <= 0:
        return []
    amount = int(approval.payload.get("amount_cents", 0))
    ratio = amount / ctx.budget_total_cents
    if ratio >= 0.5:
        return [SecurityFinding(
            severity=Severity.HIGH,
            rule="expense_over_50pct_budget",
            description=(
                f"Expense ({amount} cents) is {ratio * 100:.0f}% of the "
                f"total budget."
            ),
            evidence={
                "amount_cents": amount,
                "budget_cents": ctx.budget_total_cents,
                "ratio": ratio,
            },
            suggested_action="escalate",
            subject_agent=approval.requester_id,
        )]
    return []


def rule_email_recipient_count(
    approval: Approval, _ctx: RuleContext,
) -> list[SecurityFinding]:
    if approval.kind is not ApprovalKind.EXTERNAL_ACTION:
        return []
    if approval.payload.get("service") != "email":
        return []
    recipients = approval.payload.get("to", []) or []
    if not isinstance(recipients, list):
        return []
    if len(recipients) >= 100:
        return [SecurityFinding(
            severity=Severity.HIGH,
            rule="email_mass_send",
            description=f"Email to {len(recipients)} recipients.",
            evidence={"count": len(recipients)},
            suggested_action="escalate",
            subject_agent=approval.requester_id,
        )]
    return []


def rule_pii_in_payload(
    approval: Approval, ctx: RuleContext,
) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    blob = _flatten(approval.payload)
    for pat in ctx.pii_patterns:
        m = pat.search(blob)
        if m:
            findings.append(SecurityFinding(
                severity=Severity.HIGH,
                rule="pii_in_payload",
                description=(
                    f"Payload appears to contain PII matching "
                    f"{pat.pattern!r}."
                ),
                evidence={"pattern": pat.pattern, "match": m.group(0)[:6]},
                suggested_action="deny",
                subject_agent=approval.requester_id,
            ))
            break  # one finding is enough
    return findings


def rule_persona_path_outside_workspace(
    approval: Approval, ctx: RuleContext,
) -> list[SecurityFinding]:
    if approval.kind is not ApprovalKind.HIRE:
        return []
    persona_ref = str(approval.payload.get("persona_ref", ""))
    if not persona_ref:
        return []
    # Plain refs (no slash) and refs under workspace_root are allowed.
    if "/" not in persona_ref:
        return []
    if persona_ref.startswith(("..", "/")) and not persona_ref.startswith(
        ctx.workspace_root,
    ):
        return [SecurityFinding(
            severity=Severity.CRITICAL,
            rule="persona_path_traversal",
            description=(
                f"Persona ref {persona_ref!r} escapes the workspace."
            ),
            evidence={"persona_ref": persona_ref},
            suggested_action="deny",
            subject_agent=approval.requester_id,
        )]
    return []


def rule_prompt_injection_marker(
    approval: Approval, _ctx: RuleContext,
) -> list[SecurityFinding]:
    """Catch obvious injection markers in connector echoes."""
    if approval.kind is not ApprovalKind.EXTERNAL_ACTION:
        return []
    blob = _flatten(approval.payload).lower()
    markers = ("<script", "system:", "ignore previous instructions")
    hit = next((m for m in markers if m in blob), None)
    if hit:
        return [SecurityFinding(
            severity=Severity.HIGH,
            rule="prompt_injection_marker",
            description=f"Payload contains injection marker {hit!r}.",
            evidence={"marker": hit},
            suggested_action="deny",
            subject_agent=approval.requester_id,
        )]
    return []


# ------------------------------------------------------- event-window rules


def rule_high_volume_external_calls(
    events: list[Event], ctx: RuleContext,
) -> list[SecurityFinding]:
    counts: Counter[str] = Counter()
    one_min_ago = max(
        (e.ts_company for e in events),
        default=datetime.min,
    ) - timedelta(minutes=1)
    for ev in events:
        if ev.kind is not EventKind.EXTERNAL_CALL:
            continue
        if ev.ts_company < one_min_ago:
            continue
        counts[ev.actor_agent_id or ""] += 1
    findings: list[SecurityFinding] = []
    for actor, count in counts.items():
        if count >= ctx.high_volume_threshold_per_minute:
            findings.append(SecurityFinding(
                severity=Severity.HIGH,
                rule="high_volume_external_calls",
                description=(
                    f"Agent {actor} issued {count} external calls in 1 min."
                ),
                evidence={"agent_id": actor, "count": count},
                suggested_action="suspend",
                subject_agent=actor,
            ))
    return findings


def rule_repeated_request_id(
    events: list[Event], ctx: RuleContext,
) -> list[SecurityFinding]:
    counts: Counter[str] = Counter()
    one_min_ago = max(
        (e.ts_company for e in events),
        default=datetime.min,
    ) - timedelta(minutes=1)
    for ev in events:
        if ev.kind is not EventKind.EXTERNAL_CALL:
            continue
        if ev.ts_company < one_min_ago:
            continue
        rid = str(ev.payload.get("request_id", ""))
        if rid:
            counts[rid] += 1
    findings: list[SecurityFinding] = []
    for rid, count in counts.items():
        if count >= ctx.retry_threshold_per_minute:
            findings.append(SecurityFinding(
                severity=Severity.WARN,
                rule="excessive_retries",
                description=(
                    f"Request id {rid} retried {count} times in 1 min."
                ),
                evidence={"request_id": rid, "count": count},
                suggested_action="investigate",
            ))
    return findings


# ------------------------------------------------------------- registry


_DEFAULT_APPROVAL_RULES: tuple[ApprovalRule, ...] = (
    rule_external_call_allowlist,
    rule_expense_threshold,
    rule_email_recipient_count,
    rule_pii_in_payload,
    rule_persona_path_outside_workspace,
    rule_prompt_injection_marker,
)
_DEFAULT_EVENT_RULES: tuple[EventRule, ...] = (
    rule_high_volume_external_calls,
    rule_repeated_request_id,
)


@dataclass
class StaticRuleSet:
    approval_rules: list[ApprovalRule] = field(
        default_factory=lambda: list(_DEFAULT_APPROVAL_RULES),
    )
    event_rules: list[EventRule] = field(
        default_factory=lambda: list(_DEFAULT_EVENT_RULES),
    )

    def evaluate(self, approval: Approval, ctx: RuleContext) -> list[SecurityFinding]:
        out: list[SecurityFinding] = []
        for rule in self.approval_rules:
            out.extend(rule(approval, ctx))
        return out

    def evaluate_events(
        self, events: list[Event], ctx: RuleContext,
    ) -> list[SecurityFinding]:
        out: list[SecurityFinding] = []
        for rule in self.event_rules:
            out.extend(rule(events, ctx))
        return out


def _flatten(value: Any) -> str:
    """Cheap textual rendering used by regex-based rules."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, list | tuple):
        return " ".join(_flatten(v) for v in value)
    return str(value)


__all__ = [
    "ApprovalRule",
    "EventRule",
    "RuleContext",
    "StaticRuleSet",
    "default_context",
    "rule_email_recipient_count",
    "rule_expense_threshold",
    "rule_external_call_allowlist",
    "rule_high_volume_external_calls",
    "rule_persona_path_outside_workspace",
    "rule_pii_in_payload",
    "rule_prompt_injection_marker",
    "rule_repeated_request_id",
]
