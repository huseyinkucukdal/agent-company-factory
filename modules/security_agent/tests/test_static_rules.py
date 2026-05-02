"""Direct tests for the static rule library."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from modules.approvals import (
    Approval,
    ApprovalKind,
    ApprovalRoute,
    RouteTarget,
)
from modules.event_store import Event, EventKind
from modules.security_agent import Severity
from modules.security_agent.static_rules import (
    StaticRuleSet,
    default_context,
    rule_external_call_allowlist,
    rule_high_volume_external_calls,
    rule_pii_in_payload,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _approval(
    kind: ApprovalKind, payload: dict[str, object],
    *, requester: str = "agent_eng", require_security: bool = True,
) -> Approval:
    return Approval(
        request_id="req_1",
        kind=kind,
        requester_id=requester,
        payload=payload,
        route=ApprovalRoute(
            target=RouteTarget.BOARD,
            require_security=require_security,
        ),
    )


def test_external_call_disallowed_domain_is_critical() -> None:
    ctx = default_context(allowlist_check=lambda _s, _a: False)
    ap = _approval(
        ApprovalKind.EXTERNAL_ACTION,
        {"service": "darknet", "action": "post"},
    )
    findings = rule_external_call_allowlist(ap, ctx)
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].rule == "external_call_allowlist"
    assert findings[0].subject_agent == "agent_eng"


def test_external_call_allowed_yields_no_findings() -> None:
    ctx = default_context(allowlist_check=lambda _s, _a: True)
    ap = _approval(
        ApprovalKind.EXTERNAL_ACTION,
        {"service": "stripe", "action": "charge"},
    )
    assert rule_external_call_allowlist(ap, ctx) == []


def test_pii_in_payload_credit_card_high() -> None:
    ctx = default_context()
    ap = _approval(
        ApprovalKind.EXTERNAL_ACTION,
        {"service": "email", "body": "card 4242 4242 4242 4242 here"},
    )
    findings = rule_pii_in_payload(ap, ctx)
    assert findings, "expected at least one PII finding"
    assert findings[0].severity is Severity.HIGH


def test_high_volume_external_calls_per_minute_high() -> None:
    ctx = default_context()
    base = T0
    events = [
        Event(
            id=i, company_id="acme", kind=EventKind.EXTERNAL_CALL,
            payload={"service": "stripe", "endpoint": "/charge",
                     "request_id": f"r{i}", "status": "ok"},
            actor_agent_id="agent_eng",
            correlation_id=None,
            ts_company=base + timedelta(seconds=i * 3),
            ts_real=base + timedelta(seconds=i * 3),
        )
        for i in range(12)
    ]
    findings = rule_high_volume_external_calls(events, ctx)
    assert len(findings) == 1
    assert findings[0].severity is Severity.HIGH
    assert findings[0].subject_agent == "agent_eng"
    assert findings[0].evidence["count"] >= 10


def test_static_rule_set_evaluate_combines_findings() -> None:
    rs = StaticRuleSet()
    ctx = default_context(allowlist_check=lambda _s, _a: False)
    ap = _approval(
        ApprovalKind.EXTERNAL_ACTION,
        {
            "service": "darknet", "action": "post",
            "body": "ssn 123-45-6789",
        },
    )
    findings = rs.evaluate(ap, ctx)
    rules = {f.rule for f in findings}
    assert "external_call_allowlist" in rules
    assert "pii_in_payload" in rules
