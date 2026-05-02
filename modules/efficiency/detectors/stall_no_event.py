"""``stall.no_event`` — an agent that *did* something has gone quiet for too long."""
from __future__ import annotations

from modules.event_store import Event

from ..models import FindingCandidate, Severity, SubjectType
from .base import DetectorContext

CODE = "stall.no_event"


def _last_event_per_agent(events: list[Event]) -> dict[str, Event]:
    last: dict[str, Event] = {}
    for e in events:
        actor = e.actor_agent_id
        if not actor:
            continue
        prev = last.get(actor)
        if prev is None or e.ts_company > prev.ts_company:
            last[actor] = e
    return last


def detect(ctx: DetectorContext) -> list[FindingCandidate]:
    cfg = ctx.config.stall_no_event
    last = _last_event_per_agent(ctx.events)
    out: list[FindingCandidate] = []
    for agent_id, evt in last.items():
        gap = (ctx.now - evt.ts_company).total_seconds()
        if gap < cfg.window_seconds:
            continue
        out.append(
            FindingCandidate(
                detector_code=CODE,
                severity=Severity.WARN,
                subject_type=SubjectType.AGENT,
                subject_id=agent_id,
                evidence={
                    "last_event_id": evt.id,
                    "last_event_kind": evt.kind.value,
                    "gap_seconds": int(gap),
                    "window_seconds": cfg.window_seconds,
                },
                recommendation=(
                    f"Agent {agent_id} has produced no activity for the last {int(gap) // 60} minutes. "
                    "Is it being assigned tasks, or is it waiting?"
                ),
            )
        )
    return out
