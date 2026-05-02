# Module 12 — Security Agent

## Purpose

A special behaviour layer on top of the standard Agent Runtime. Monitors security rules, has veto rights over risky actions, reports directly to the Board. Actions approved by other agents do not bypass this (when the require_security flag is active).

## Rules you defined (summary)

- Required at company bootstrap: CEO + HR + **Security**
- Any action not approved by Security **cannot be performed** (non-overridable, not even by CEO)
- Security has direct access to the Board (the only special role alongside HR)
- Reports threats directly to the Board
- Actively scans on its own (event log, behaviour anomalies)

## Responsibility boundaries

**Inside:**
- Security persona prompt template
- Approval pre-veto flow (reviews all requests with `require_security=True`)
- Proactive monitoring: anomaly detection in the event stream
- Usage of `report_to_board` tool
- Allowlist enforcement: logs when agents attempt to call out-of-allowlist services
- Additional constraints for itself: Security cannot fire itself or anyone else (must go through the Board)

**Outside:**
- Standard Agent Runtime mechanics (already Module 10)
- Approval queue (Approvals already handles it)
- Connector's allowlist enforcement (Connector handles it; Security is the upper-layer policy reviewer)

## Dependencies

| Module | How |
|---|---|
| Agent Runtime | to create the security agent |
| Approvals | security decision as a sub-approval |
| Event Store | proactive scanning |
| Identity | special role guards |
| Connector | allowlist policy suggestion |

## Public API

No new Python class for Security Agent — **special persona + role + tool whitelist**. Additional helpers:

```python
class SecurityPolicy:
    """Holds the rules for a security agent within a single company."""
    def __init__(self, agent_id: str, deps): ...

    async def review_approval(self, approval: Approval) -> Decision:
        """Hook called by the Approvals module for requests with
        require_security=True. Async — a sub-approval is sent to the
        security agent and the decision is returned. Or policy-based
        automatic decision."""

    async def scan_recent_events(self, window_minutes: int = 5) -> list[SecurityFinding]:
        """Periodic scan. Anomaly detection."""

    async def auto_flag(self, finding: SecurityFinding) -> None:
        """Finding is sent to Board as a SECURITY_FLAG approval or report."""

@dataclass(frozen=True)
class SecurityFinding:
    severity: Severity
    rule: str
    description: str
    evidence: dict
    suggested_action: str

class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    HIGH = "high"
    CRITICAL = "critical"
```

## Veto mechanism

When the Approvals module has a request with `require_security=True`, it creates a sub-approval. The sub-approval arrives in the Security agent's inbox as a `SECURITY_REVIEW` message. The Security agent's persona makes an LLM decision OR some policies trigger an automatic decision (e.g. domain `xyz` not in allowlist → automatic DENY).

**Hybrid:** the automatic policy filter runs first:
- If the rule is a clear violation → automatic DENY (no LLM call, fast)
- Ambiguous → ask the LLM

```python
async def review_approval(approval):
    findings = run_static_rules(approval)  # allowlist, threshold, etc.
    if any(f.severity == CRITICAL for f in findings):
        return DENY(reason=combined(findings))
    if all(f.severity == INFO for f in findings):
        return APPROVE  # low risk, auto-pass
    # ambiguous — ask security agent via message
    return await ask_security_agent_via_message(approval, findings)
```

## Static rules (examples)

| Rule | Severity |
|---|---|
| External call domain not in allowlist | CRITICAL |
| 10+ external calls from same agent in 1 minute | HIGH |
| 50% of budget in a single expense | HIGH |
| Email "to" has 100+ recipients | HIGH |
| New agent persona file path outside workspace | CRITICAL |
| Connector response has `<script>` or `system:` injection pattern | HIGH |
| 5+ retries with same request_id in a minute | WARN |
| Approval payload contains PII-like data (credit card, SSN regex) | HIGH |

Rules can be extended from config.

## Proactive scanning

Background task scans the Event Store every 5 minutes:
```
events = events.read(since=last_scan, kinds=[
    EXPENSE_CHARGED, EXTERNAL_CALL, AGENT_CREATED, BUDGET_WARNING, ...
])
findings = run_static_rules_on_window(events)
for f in findings:
    if f.severity in (HIGH, CRITICAL):
        await auto_flag(f)
```

`auto_flag`:
- HIGH → SECURITY_FLAG approval, routed to Board
- CRITICAL → simultaneously: SECURITY_FLAG to Board + suspend the offending agent (call `restart_agent` or `pause_agent` on Orchestrator)

## Special role guards

- Security cannot fire itself
- Security cannot fire the CEO (nobody can)
- Security can be fired by its manager without Board approval
- Security has the `report_to_board` tool (HR also has it)
- Security has access to proactive scanning tools (event log read); other agents are restricted

## Persona prompt skeleton (fragment)

```
You are the Security Agent at {{company_name}}. Your sole responsibilities:

1. Review every action that requires security approval. Be strict but fair.
2. Watch for anomalies in spending, external calls, hiring patterns.
3. Report severe risks directly to the Board using `report_to_board`.
4. Never approve actions that violate the company allowlist or budget controls.

You do not produce business value, you protect it.

Static rules already auto-flag: {{static_rules_summary}}.
You only see what those rules couldn't decide on.

When reviewing, your decision must be:
- APPROVE with note (low residual risk)
- DENY with explicit reason
- ESCALATE to Board with question
```

## Test scenarios

1. `test_static_rule_external_disallowed_domain_critical_deny`
2. `test_static_rule_high_volume_external_calls_high_flag`
3. `test_review_approval_pii_in_payload_denied`
4. `test_review_approval_routine_low_risk_auto_approved`
5. `test_review_approval_ambiguous_routes_to_llm`
6. `test_proactive_scan_finds_anomaly_emits_flag`
7. `test_critical_flag_suspends_offending_agent`
8. `test_security_cannot_fire_anyone`
9. `test_security_can_be_fired_by_manager_without_board_approval`
10. `test_report_to_board_only_security_and_hr`
11. `test_security_veto_overrides_board_approval` — order: security first
12. `test_security_approve_lets_board_decide`

## Error classes

```python
class SecurityError(Exception): ...
class PolicyViolation(SecurityError): ...
```

## Definition of Done

- [ ] Static rule set implemented and tested
- [ ] Sub-approval review flow working (with approvals mock)
- [ ] Proactive scanning background task
- [ ] Critical finding triggers agent suspend
- [ ] Persona template ready
- [ ] Special role guards (cannot fire self/CEO)
- [ ] Coverage ≥ 85%

## File skeleton

```
modules/security_agent/
├── PLAN.md
├── __init__.py
├── policy.py             # SecurityPolicy
├── static_rules.py
├── proactive_scan.py
├── persona.md            # template
├── exceptions.py
└── tests/
    ├── test_static_rules.py
    ├── test_review.py
    ├── test_proactive.py
    └── test_role_guards.py
```
