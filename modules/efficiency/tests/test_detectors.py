"""End-to-end detector tests using a real EventStore.

We feed synthetic events through the EventStore (so timestamps and
payloads are validated for real), then invoke ``service.tick()`` and
verify the resulting findings.
"""
from __future__ import annotations

from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    Approvals,
    Decision,
    RouteTarget,
)
from modules.efficiency import (
    EfficiencyService,
    FindingStatus,
    Severity,
    SubjectType,
)
from modules.event_store import EventKind, EventStore

from .conftest import FakeClock


def _appended(es: EventStore, kind: EventKind, payload: dict[str, object], *,
              actor: str | None = None) -> None:
    es.append(kind, payload, actor=actor)


# --------------------------------------------------------- loop.tool_repeat


def test_loop_tool_repeat_fires_after_threshold(
    service: EfficiencyService, event_store: EventStore, clock: FakeClock
) -> None:
    for i in range(4):
        _appended(
            event_store, EventKind.TOOL_CALLED,
            {"tool": "search_web", "arguments": {}, "request_id": f"r{i}"},
            actor="agent-1",
        )
    candidates = service.tick()
    assert any(c.detector_code == "loop.tool_repeat" for c in candidates)
    findings = service.store.list(status=FindingStatus.OPEN)
    target = [f for f in findings if f.detector_code == "loop.tool_repeat"]
    assert len(target) == 1
    assert target[0].subject_type is SubjectType.AGENT
    assert target[0].subject_id == "agent-1"


def test_loop_tool_repeat_stays_silent_below_threshold(
    service: EfficiencyService, event_store: EventStore
) -> None:
    for i in range(3):
        _appended(
            event_store, EventKind.TOOL_CALLED,
            {"tool": "search_web", "arguments": {}, "request_id": f"r{i}"},
            actor="agent-1",
        )
    service.tick()
    assert service.store.list(detector_code="loop.tool_repeat") == []


def test_loop_tool_repeat_auto_closes_when_window_passes(
    service: EfficiencyService, event_store: EventStore, clock: FakeClock
) -> None:
    for i in range(4):
        _appended(
            event_store, EventKind.TOOL_CALLED,
            {"tool": "search_web", "arguments": {}, "request_id": f"r{i}"},
            actor="agent-1",
        )
    service.tick()
    assert service.store.list_open(detector_code="loop.tool_repeat")

    # Advance well past the 5-min window so the events drop out.
    clock.advance(minutes=10)
    service.tick()

    assert service.store.list_open(detector_code="loop.tool_repeat") == []


# --------------------------------------------------------- tool.error_storm


def test_tool_error_storm_fires(
    service: EfficiencyService, event_store: EventStore
) -> None:
    for i in range(7):
        _appended(
            event_store, EventKind.TOOL_RESULT,
            {"tool": "send_email", "request_id": f"r{i}", "ok": False,
             "error": "smtp"},
            actor="agent-1",
        )
    for i in range(7, 12):
        _appended(
            event_store, EventKind.TOOL_RESULT,
            {"tool": "send_email", "request_id": f"r{i}", "ok": True,
             "result": {}},
            actor="agent-1",
        )

    service.tick()
    findings = service.store.list(detector_code="tool.error_storm")
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].subject_id == "send_email"


def test_tool_error_storm_silent_below_min_calls(
    service: EfficiencyService, event_store: EventStore
) -> None:
    for i in range(5):
        _appended(
            event_store, EventKind.TOOL_RESULT,
            {"tool": "send_email", "request_id": f"r{i}", "ok": False,
             "error": "x"},
            actor="agent-1",
        )
    service.tick()
    assert service.store.list(detector_code="tool.error_storm") == []


# --------------------------------------------------------- stall.no_event


def test_stall_no_event_fires_after_gap(
    service: EfficiencyService, event_store: EventStore, clock: FakeClock
) -> None:
    _appended(
        event_store, EventKind.TOOL_CALLED,
        {"tool": "x", "arguments": {}, "request_id": "r1"},
        actor="agent-1",
    )
    clock.advance(minutes=20)
    service.tick()
    findings = service.store.list(detector_code="stall.no_event")
    assert any(f.subject_id == "agent-1" for f in findings)


def test_stall_no_event_silent_when_recent(
    service: EfficiencyService, event_store: EventStore, clock: FakeClock
) -> None:
    _appended(
        event_store, EventKind.TOOL_CALLED,
        {"tool": "x", "arguments": {}, "request_id": "r1"},
        actor="agent-1",
    )
    clock.advance(minutes=2)
    service.tick()
    assert service.store.list(detector_code="stall.no_event") == []


# --------------------------------------------------------- approvals.backlog


def test_approvals_backlog_fires_for_aged_pending(
    service: EfficiencyService, approvals: Approvals, clock: FakeClock,
) -> None:
    approvals.request(
        kind=ApprovalKind.EXPENSE,
        requester_id="agent-1",
        payload={"note": "domain"},
        route=ApprovalRoute(target=RouteTarget.BOARD),
    )
    clock.advance(minutes=45)
    service.tick()
    findings = service.store.list(detector_code="approvals.backlog")
    assert len(findings) == 1
    assert findings[0].subject_type is SubjectType.COMPANY


def test_approvals_backlog_silent_when_fresh(
    service: EfficiencyService, approvals: Approvals, clock: FakeClock,
) -> None:
    approvals.request(
        kind=ApprovalKind.EXPENSE,
        requester_id="agent-1",
        payload={"note": "domain"},
        route=ApprovalRoute(target=RouteTarget.BOARD),
    )
    clock.advance(minutes=5)
    service.tick()
    assert service.store.list(detector_code="approvals.backlog") == []


def test_approvals_backlog_auto_closes_after_decision(
    service: EfficiencyService, approvals: Approvals, clock: FakeClock,
) -> None:
    approval = approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="agent-1",
        payload={"role": "engineer"},
        route=ApprovalRoute(target=RouteTarget.BOARD),
    )
    clock.advance(minutes=45)
    service.tick()
    assert service.store.list_open(detector_code="approvals.backlog")

    approvals.decide(
        request_id=approval.request_id,
        decider_id="board",
        decision=Decision.APPROVE,
    )
    service.tick()
    assert service.store.list_open(detector_code="approvals.backlog") == []


# --------------------------------------------------------- cost.no_output


def test_cost_no_output_fires(
    service: EfficiencyService, event_store: EventStore
) -> None:
    _appended(
        event_store, EventKind.EXPENSE_CHARGED,
        {"amount_cents": 700, "category": "tokens", "request_id": "r1"},
    )
    service.tick()
    findings = service.store.list(detector_code="cost.no_output")
    assert len(findings) == 1
    assert findings[0].subject_type is SubjectType.COMPANY


def test_cost_no_output_silent_with_messages(
    service: EfficiencyService, event_store: EventStore
) -> None:
    _appended(
        event_store, EventKind.EXPENSE_CHARGED,
        {"amount_cents": 700, "category": "tokens", "request_id": "r1"},
    )
    _appended(
        event_store, EventKind.MESSAGE_SENT,
        {"from_agent": "a", "to_agent": "b", "content": "hi"},
    )
    service.tick()
    assert service.store.list(detector_code="cost.no_output") == []


# --------------------------------------------------------- org.manager_bottleneck


def test_org_manager_bottleneck_fires(
    service: EfficiencyService, approvals: Approvals
) -> None:
    # Five approvals, four routed to manager-1, one to manager-2.
    for i in range(4):
        approvals.request(
            kind=ApprovalKind.HIRE,
            requester_id="hr",
            payload={"role": "engineer", "seq": i},
            route=ApprovalRoute(
                target=RouteTarget.AGENT, agent_id="manager-1"
            ),
        )
    approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id="hr",
        payload={"role": "engineer"},
        route=ApprovalRoute(
            target=RouteTarget.AGENT, agent_id="manager-2"
        ),
    )

    service.tick()
    findings = service.store.list(detector_code="org.manager_bottleneck")
    assert len(findings) == 1
    assert findings[0].subject_id == "manager-1"
