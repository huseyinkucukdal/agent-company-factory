"""Tests for SecurityPolicy.review_approval."""
from __future__ import annotations

import pytest

from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    Approvals,
    ApprovalStatus,
    Decision,
    RouteTarget,
)
from modules.event_store import EventKind, EventStore
from modules.security_agent import (
    ReviewOutcome,
    SecurityDeps,
    SecurityPolicy,
    Severity,
)
from modules.security_agent.models import SecurityFinding
from modules.security_agent.static_rules import (
    RuleContext,
    StaticRuleSet,
    default_context,
)


@pytest.mark.asyncio
async def test_routine_request_auto_approved(
    policy: SecurityPolicy, approvals: Approvals,
) -> None:
    ap = approvals.request(
        kind=ApprovalKind.EXTERNAL_ACTION,
        requester_id="agent_eng",
        payload={"service": "stripe", "action": "charge"},
        route=ApprovalRoute(
            target=RouteTarget.BOARD, require_security=True,
        ),
    )
    result = await policy.review_approval(ap)
    assert result.outcome is ReviewOutcome.APPROVED
    refreshed = approvals.get(ap.request_id)
    assert refreshed.security_decision is Decision.APPROVE
    assert refreshed.status is ApprovalStatus.PENDING  # board still decides


@pytest.mark.asyncio
async def test_critical_finding_denies_and_emits_veto(
    approvals: Approvals, events: EventStore, orch: object,
    rules: StaticRuleSet, bootstrap: dict[str, str],
) -> None:
    # Custom context: allowlist denies everything → CRITICAL.
    deps = SecurityDeps(
        approvals=approvals, events=events,
        orchestrator=orch,  # type: ignore[arg-type]
        rules=rules,
        rule_context_factory=lambda: default_context(
            allowlist_check=lambda _s, _a: False,
        ),
    )
    pol = SecurityPolicy(bootstrap["sec"], deps)
    ap = approvals.request(
        kind=ApprovalKind.EXTERNAL_ACTION,
        requester_id=bootstrap["eng"],
        payload={"service": "darknet", "action": "post"},
        route=ApprovalRoute(
            target=RouteTarget.BOARD, require_security=True,
        ),
    )
    result = await pol.review_approval(ap)
    assert result.outcome is ReviewOutcome.DENIED
    assert any(f.severity is Severity.CRITICAL for f in result.findings)
    refreshed = approvals.get(ap.request_id)
    assert refreshed.status is ApprovalStatus.DENIED
    veto_events = events.read(kinds=[EventKind.SECURITY_VETO])
    assert len(veto_events) == 1
    assert veto_events[0].payload["target"] == bootstrap["eng"]


@pytest.mark.asyncio
async def test_high_finding_without_llm_is_deferred(
    approvals: Approvals, events: EventStore, rules: StaticRuleSet,
    bootstrap: dict[str, str],
) -> None:
    deps = SecurityDeps(
        approvals=approvals, events=events,
        rules=rules,
        rule_context_factory=lambda: default_context(
            budget_total_cents=10_000,
        ),
        llm_reviewer=None,
    )
    pol = SecurityPolicy(bootstrap["sec"], deps)
    ap = approvals.request(
        kind=ApprovalKind.EXPENSE,
        requester_id=bootstrap["eng"],
        payload={"amount_cents": 8_000, "category": "ads"},
        route=ApprovalRoute(
            target=RouteTarget.BOARD, require_security=True,
        ),
    )
    result = await pol.review_approval(ap)
    assert result.outcome is ReviewOutcome.DEFERRED
    refreshed = approvals.get(ap.request_id)
    # Nothing recorded yet — awaiting human/LLM judgement.
    assert refreshed.security_decision is None
    assert refreshed.status is ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_high_finding_with_llm_approve_lets_board_decide(
    approvals: Approvals, events: EventStore, rules: StaticRuleSet,
    bootstrap: dict[str, str],
) -> None:
    async def reviewer(_a: object, _f: list[SecurityFinding]) -> tuple[Decision, str]:
        return Decision.APPROVE, "manual_ok"

    deps = SecurityDeps(
        approvals=approvals, events=events,
        rules=rules,
        rule_context_factory=lambda: default_context(budget_total_cents=10_000),
        llm_reviewer=reviewer,
    )
    pol = SecurityPolicy(bootstrap["sec"], deps)
    ap = approvals.request(
        kind=ApprovalKind.EXPENSE,
        requester_id=bootstrap["eng"],
        payload={"amount_cents": 9_000, "category": "ads"},
        route=ApprovalRoute(
            target=RouteTarget.BOARD, require_security=True,
        ),
    )
    result = await pol.review_approval(ap)
    assert result.outcome is ReviewOutcome.APPROVED
    refreshed = approvals.get(ap.request_id)
    assert refreshed.security_decision is Decision.APPROVE
    assert refreshed.status is ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_high_finding_with_llm_deny_blocks_request(
    approvals: Approvals, events: EventStore, rules: StaticRuleSet,
    bootstrap: dict[str, str],
) -> None:
    async def reviewer(_a: object, _f: list[SecurityFinding]) -> tuple[Decision, str]:
        return Decision.DENY, "looks_fishy"

    deps = SecurityDeps(
        approvals=approvals, events=events,
        rules=rules,
        rule_context_factory=lambda: default_context(budget_total_cents=10_000),
        llm_reviewer=reviewer,
    )
    pol = SecurityPolicy(bootstrap["sec"], deps)
    ap = approvals.request(
        kind=ApprovalKind.EXPENSE,
        requester_id=bootstrap["eng"],
        payload={"amount_cents": 9_000, "category": "ads"},
        route=ApprovalRoute(
            target=RouteTarget.BOARD, require_security=True,
        ),
    )
    result = await pol.review_approval(ap)
    assert result.outcome is ReviewOutcome.DENIED
    refreshed = approvals.get(ap.request_id)
    assert refreshed.status is ApprovalStatus.DENIED
    assert events.read(kinds=[EventKind.SECURITY_VETO])


@pytest.mark.asyncio
async def test_pii_in_payload_is_high_and_deferred_without_llm(
    policy: SecurityPolicy, approvals: Approvals,
) -> None:
    ap = approvals.request(
        kind=ApprovalKind.EXTERNAL_ACTION,
        requester_id="agent_eng",
        payload={
            "service": "email", "action": "send",
            "body": "ssn 123-45-6789",
        },
        route=ApprovalRoute(
            target=RouteTarget.BOARD, require_security=True,
        ),
    )
    result = await policy.review_approval(ap)
    assert result.outcome is ReviewOutcome.DEFERRED
    assert any(f.rule == "pii_in_payload" for f in result.findings)


# Reference unused symbols so type checkers can see they are used in helpers.
_ = RuleContext
