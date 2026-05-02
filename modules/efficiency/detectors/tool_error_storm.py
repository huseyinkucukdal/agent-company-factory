"""``tool.error_storm`` — a single tool failing past a threshold rate."""
from __future__ import annotations

from collections import defaultdict

from modules.event_store import EventKind

from ..models import FindingCandidate, Severity, SubjectType
from .base import DetectorContext

CODE = "tool.error_storm"


def detect(ctx: DetectorContext) -> list[FindingCandidate]:
    cfg = ctx.config.tool_error_storm
    recent = [
        e for e in ctx.events
        if e.kind is EventKind.TOOL_RESULT
        and (ctx.now - e.ts_company).total_seconds() <= cfg.window_seconds
    ]
    totals: dict[str, int] = defaultdict(int)
    errors: dict[str, int] = defaultdict(int)
    samples: dict[str, list[int]] = defaultdict(list)
    for e in recent:
        tool = str(e.payload.get("tool", ""))
        if not tool:
            continue
        totals[tool] += 1
        if not bool(e.payload.get("ok", True)):
            errors[tool] += 1
            samples[tool].append(e.id)

    out: list[FindingCandidate] = []
    for tool, total in totals.items():
        err = errors.get(tool, 0)
        if total < cfg.min_calls or err == 0:
            continue
        rate = err / total
        if rate < cfg.error_rate:
            continue
        out.append(
            FindingCandidate(
                detector_code=CODE,
                severity=Severity.CRITICAL,
                subject_type=SubjectType.TOOL,
                subject_id=tool,
                evidence={
                    "total_calls": total,
                    "error_count": err,
                    "error_rate": round(rate, 3),
                    "window_seconds": cfg.window_seconds,
                    "sample_event_ids": samples[tool][-5:],
                },
                recommendation=(
                    f"Tool `{tool}` returned {err}/{total} errors "
                    f"({int(rate * 100)}%) in the last {cfg.window_seconds // 60} minutes. "
                    "Check the Connector / external service."
                ),
            )
        )
    return out
