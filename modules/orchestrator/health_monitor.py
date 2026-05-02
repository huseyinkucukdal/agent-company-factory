"""Per-agent health score derived from :class:`HealthSignals`."""
from __future__ import annotations

from datetime import datetime, timedelta

from modules.agent_runtime import HealthSignals


def compute_score(signals: HealthSignals, *, now: datetime) -> tuple[float, list[str]]:
    """Return ``(score, notes)`` where score is in ``[0.0, 1.0]``.

    Notes describe each penalty applied (for the alert payload).
    """
    score = 1.0
    notes: list[str] = []

    if signals.consecutive_errors > 3:
        score -= 0.3
        notes.append(f"errors:{signals.consecutive_errors}")

    if signals.parse_failures > 0 and signals.total_turns > 0:
        ratio = signals.parse_failures / signals.total_turns
        if ratio > 0.5:
            score -= 0.3
            notes.append(f"parse_rate:{ratio:.2f}")

    if signals.total_turns > 0:
        repeat_ratio = signals.repeat_response_count / signals.total_turns
        if repeat_ratio > 0.7:
            score -= 0.4
            notes.append(f"repeat_ratio:{repeat_ratio:.2f}")

    if (now - signals.last_heartbeat) > timedelta(minutes=5):
        score -= 0.5
        notes.append("stale_heartbeat")

    return max(0.0, min(1.0, score)), notes


UNHEALTHY_CUTOFF = 0.4


__all__ = ["UNHEALTHY_CUTOFF", "compute_score"]
