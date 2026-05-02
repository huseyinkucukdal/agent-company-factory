"""``cost.no_output`` — money spent in window with zero agent messaging output.

Heuristic: if EXPENSE_CHARGED amounts in the window total above the
threshold and the same window contains no MESSAGE_SENT events, the
company is "burning without producing".
"""
from __future__ import annotations

from modules.event_store import EventKind

from ..models import FindingCandidate, Severity, SubjectType
from .base import DetectorContext

CODE = "cost.no_output"


def detect(ctx: DetectorContext) -> list[FindingCandidate]:
    cfg = ctx.config.cost_no_output
    recent = [
        e for e in ctx.events
        if (ctx.now - e.ts_company).total_seconds() <= cfg.window_seconds
    ]
    spend_cents = 0
    expense_count = 0
    message_count = 0
    for e in recent:
        if e.kind is EventKind.EXPENSE_CHARGED:
            try:
                spend_cents += int(e.payload.get("amount_cents", 0))
            except (TypeError, ValueError):
                continue
            expense_count += 1
        elif e.kind is EventKind.MESSAGE_SENT:
            message_count += 1

    spend_usd = spend_cents / 100.0
    if spend_usd < cfg.min_spend_usd or message_count > 0:
        return []

    return [
        FindingCandidate(
            detector_code=CODE,
            severity=Severity.WARN,
            subject_type=SubjectType.COMPANY,
            subject_id=None,
            evidence={
                "spend_usd": round(spend_usd, 2),
                "expense_count": expense_count,
                "message_count": message_count,
                "window_seconds": cfg.window_seconds,
            },
            recommendation=(
                f"${spend_usd:.2f} spent ({expense_count} item(s)) "
                f"in the last {cfg.window_seconds // 60} minutes, "
                "but no agent messages produced. Is the budget being wasted?"
            ),
        )
    ]
