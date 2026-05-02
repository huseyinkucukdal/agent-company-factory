"""``org.manager_bottleneck`` — pending approvals concentrate on one manager."""
from __future__ import annotations

from collections import Counter

from ..models import FindingCandidate, Severity, SubjectType
from .base import DetectorContext

CODE = "org.manager_bottleneck"


def detect(ctx: DetectorContext) -> list[FindingCandidate]:
    if ctx.db is None:
        return []
    cfg = ctx.config.org_manager_bottleneck

    rows = ctx.db.connect().execute(
        "SELECT route_target FROM approvals WHERE status = 'pending'"
    ).fetchall()
    if len(rows) < cfg.min_total_pending:
        return []

    per_manager: Counter[str] = Counter()
    for r in rows:
        target = r["route_target"] or ""
        if target.startswith("agent:"):
            per_manager[target.split(":", 1)[1]] += 1
    if not per_manager:
        return []

    total_managed = sum(per_manager.values())
    if total_managed < cfg.min_total_pending:
        return []

    top_manager, top_count = per_manager.most_common(1)[0]
    share = top_count / total_managed
    if share < cfg.top1_share:
        return []

    return [
        FindingCandidate(
            detector_code=CODE,
            severity=Severity.WARN,
            subject_type=SubjectType.MANAGER,
            subject_id=top_manager,
            evidence={
                "manager_id": top_manager,
                "manager_pending": top_count,
                "total_pending_to_managers": total_managed,
                "share": round(share, 3),
            },
            recommendation=(
                f"Manager {top_manager} is handling "
                f"{int(share * 100)}% of all open approvals "
                f"({top_count}/{total_managed}). Load should be balanced — "
                "consider delegating to another agent or redistributing authority."
            ),
        )
    ]
