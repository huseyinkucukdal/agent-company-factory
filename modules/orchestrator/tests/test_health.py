"""Health monitor tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from modules.agent_runtime import HealthSignals
from modules.event_store import EventKind, EventStore
from modules.orchestrator import (
    UNHEALTHY_CUTOFF,
    Orchestrator,
    compute_score,
)


def _t() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def test_score_healthy_baseline() -> None:
    signals = HealthSignals(last_heartbeat=_t())
    score, notes = compute_score(signals, now=_t())
    assert score == 1.0
    assert notes == []


def test_stale_heartbeat_penalty() -> None:
    signals = HealthSignals(last_heartbeat=_t() - timedelta(minutes=30))
    score, notes = compute_score(signals, now=_t())
    assert score < UNHEALTHY_CUTOFF or "stale_heartbeat" in notes


def test_high_error_streak_penalty() -> None:
    signals = HealthSignals(last_heartbeat=_t(), consecutive_errors=10)
    score, _ = compute_score(signals, now=_t())
    assert score < 1.0


def test_orchestrator_health_sweep_emits_alert(
    orch: Orchestrator, bootstrap_org: dict[str, str],
    event_store: EventStore,
) -> None:
    eng_handle = orch._agents[bootstrap_org["eng"]]
    eng_handle.set_signals(  # type: ignore[attr-defined]
        last_heartbeat=_t() - timedelta(minutes=30),
        consecutive_errors=10,
        repeat_response_count=10,
        total_turns=10,
    )
    sweep = orch.health_sweep()
    eng_health = next(h for h in sweep if h.agent_id == bootstrap_org["eng"])
    assert eng_health.score < UNHEALTHY_CUTOFF

    alerts = event_store.read(kinds=[EventKind.AGENT_HEALTH_ALERT])
    assert any(
        a.payload.get("agent_id") == bootstrap_org["eng"] for a in alerts
    )
