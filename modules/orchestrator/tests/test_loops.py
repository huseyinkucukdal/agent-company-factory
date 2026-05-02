"""Loop detection tests (structural + semantic)."""
from __future__ import annotations

import pytest

from modules.event_store import EventKind, EventStore
from modules.orchestrator import (
    LoopDetector,
    LoopDetectorConfig,
    Orchestrator,
    SendResult,
)


def test_structural_repeat_edges() -> None:
    det = LoopDetector(LoopDetectorConfig(
        structural_threshold=3, pingpong_threshold=99,
        semantic_threshold=99,
    ))
    for i in range(2):
        looped, _ = det.check(from_agent="a", to_agent="b", content=f"q{i}")
        assert not looped
    looped, kind = det.check(from_agent="a", to_agent="b", content="q3")
    assert looped is True
    assert kind == "structural"


def test_pingpong_detection() -> None:
    det = LoopDetector(LoopDetectorConfig(
        structural_threshold=99, pingpong_threshold=3,
        semantic_threshold=99,
    ))
    last: tuple[bool, str | None] = (False, None)
    for i in range(6):
        sender, recv = ("a", "b") if i % 2 == 0 else ("b", "a")
        last = det.check(from_agent=sender, to_agent=recv, content=str(i))
    # last call should detect a→b→a→b→a→b alternating
    assert last[0] is True
    assert last[1] == "structural"


def test_semantic_repeat_detected() -> None:
    det = LoopDetector(LoopDetectorConfig(
        structural_threshold=99, pingpong_threshold=99,
        semantic_threshold=3, semantic_similarity=0.7,
    ))
    det.check(from_agent="a", to_agent="b", content="please update the report on Q1 sales")
    det.check(from_agent="a", to_agent="b", content="please update the report on Q1 sales now")
    looped, kind = det.check(from_agent="a", to_agent="b", content="please update the report Q1 sales")
    assert looped is True
    assert kind == "semantic"


@pytest.mark.asyncio
async def test_orchestrator_rejects_after_loop_and_suspends(
    orch: Orchestrator, bootstrap_org: dict[str, str],
    event_store: EventStore,
) -> None:
    ceo, eng = bootstrap_org["ceo"], bootstrap_org["eng"]

    # Push the same edge until structural threshold (5) hits.
    # capacity=3 → reset between batches by configuring high.
    orch.configure_rate_limit(ceo, capacity=10_000, refill_per_second=10_000)

    # Three loop strikes ⇒ recipient suspended.
    last = SendResult.QUEUED
    for _ in range(40):
        last = await orch.send(
            from_agent=ceo, to_agent=eng, content="please ping",
        )
        if orch.is_suspended(eng):
            break

    assert orch.is_suspended(eng) is True
    assert last in (SendResult.REJECTED_LOOP, SendResult.REJECTED_INVALID_TARGET)

    # Subsequent sends to suspended agent are rejected.
    res = await orch.send(from_agent=ceo, to_agent=eng, content="x")
    assert res is SendResult.REJECTED_INVALID_TARGET

    alerts = event_store.read(kinds=[EventKind.AGENT_HEALTH_ALERT])
    issues = {e.payload.get("issue") for e in alerts}
    assert any("loop" in (i or "") for i in issues)
