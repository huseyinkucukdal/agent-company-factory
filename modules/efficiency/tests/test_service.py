"""EfficiencyService end-to-end: subscriber + detector loop."""
from __future__ import annotations

import asyncio

from modules.efficiency import EfficiencyService, FindingStatus
from modules.event_store import EventKind, EventStore

from .conftest import FakeClock


async def test_async_loop_processes_events_and_opens_finding(
    service: EfficiencyService, event_store: EventStore
) -> None:
    # Reduce tick interval so the test is fast.
    service._tick_interval = 0.05
    await service.start()
    try:
        for i in range(4):
            event_store.append(
                EventKind.TOOL_CALLED,
                {"tool": "search_web", "arguments": {}, "request_id": f"r{i}"},
                actor="agent-1",
            )
        # Give the loop a couple of ticks to drain + run detectors.
        for _ in range(20):
            await asyncio.sleep(0.05)
            opens = service.store.list_open(detector_code="loop.tool_repeat")
            if opens:
                break
        assert service.store.list_open(detector_code="loop.tool_repeat")
    finally:
        await service.stop()


async def test_emits_finding_opened_event(
    service: EfficiencyService, event_store: EventStore
) -> None:
    for i in range(4):
        event_store.append(
            EventKind.TOOL_CALLED,
            {"tool": "search_web", "arguments": {}, "request_id": f"r{i}"},
            actor="agent-1",
        )
    service.tick()
    found = event_store.read(kinds=[EventKind.EFFICIENCY_FINDING_OPENED])
    assert len(found) >= 1
    payload = found[0].payload
    assert payload["detector_code"] == "loop.tool_repeat"
    assert payload["severity"] == "warn"
    assert payload["subject_id"] == "agent-1"


async def test_auto_close_emits_event(
    service: EfficiencyService,
    event_store: EventStore,
    clock: FakeClock,
) -> None:
    for i in range(4):
        event_store.append(
            EventKind.TOOL_CALLED,
            {"tool": "search_web", "arguments": {}, "request_id": f"r{i}"},
            actor="agent-1",
        )
    service.tick()
    clock.advance(minutes=10)
    service.tick()

    closes = event_store.read(kinds=[EventKind.EFFICIENCY_FINDING_CLOSED])
    assert len(closes) >= 1
    assert closes[0].payload["reason"] == "resolved"


async def test_manual_close_emits_event(
    service: EfficiencyService,
    event_store: EventStore,
) -> None:
    for i in range(4):
        event_store.append(
            EventKind.TOOL_CALLED,
            {"tool": "search_web", "arguments": {}, "request_id": f"r{i}"},
            actor="agent-1",
        )
    service.tick()
    finding = service.store.list_open(detector_code="loop.tool_repeat")[0]
    service.manual_close(finding.id)

    closes = event_store.read(kinds=[EventKind.EFFICIENCY_FINDING_CLOSED])
    assert any(c.payload["reason"] == "manual" for c in closes)
    assert service.store.get(finding.id).status is FindingStatus.CLOSED
