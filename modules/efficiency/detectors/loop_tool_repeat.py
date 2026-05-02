"""``loop.tool_repeat`` — same agent calls the same tool ≥ N times in a window."""
from __future__ import annotations

from collections import Counter

from modules.event_store import EventKind

from ..models import FindingCandidate, Severity, SubjectType
from .base import DetectorContext

CODE = "loop.tool_repeat"


def detect(ctx: DetectorContext) -> list[FindingCandidate]:
    cfg = ctx.config.loop_tool_repeat
    recent = [
        e for e in ctx.events
        if e.kind is EventKind.TOOL_CALLED
        and (ctx.now - e.ts_company).total_seconds() <= cfg.window_seconds
    ]
    counts: Counter[tuple[str, str]] = Counter()
    samples: dict[tuple[str, str], list[int]] = {}
    for e in recent:
        actor = e.actor_agent_id or ""
        tool = str(e.payload.get("tool", ""))
        if not actor or not tool:
            continue
        key = (actor, tool)
        counts[key] += 1
        samples.setdefault(key, []).append(e.id)

    out: list[FindingCandidate] = []
    for (actor, tool), n in counts.items():
        if n < cfg.repeat_threshold:
            continue
        out.append(
            FindingCandidate(
                detector_code=CODE,
                severity=Severity.WARN,
                subject_type=SubjectType.AGENT,
                subject_id=actor,
                evidence={
                    "tool": tool,
                    "count": n,
                    "window_seconds": cfg.window_seconds,
                    "sample_event_ids": samples[(actor, tool)][-5:],
                },
                recommendation=(
                    f"Agent {actor} called tool `{tool}` {n} times "
                    f"in the last {cfg.window_seconds // 60} minutes. "
                    "Their manager should investigate — may be stuck in a loop."
                ),
            )
        )
    return out
