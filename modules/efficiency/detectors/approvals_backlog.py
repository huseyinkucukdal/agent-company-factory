"""``approvals.backlog`` — pending approvals are aging past the threshold."""
from __future__ import annotations

from datetime import datetime

from ..models import FindingCandidate, Severity, SubjectType
from .base import DetectorContext

CODE = "approvals.backlog"


def detect(ctx: DetectorContext) -> list[FindingCandidate]:
    if ctx.db is None:
        return []
    cfg = ctx.config.approvals_backlog
    rows = ctx.db.connect().execute(
        "SELECT request_id, kind, created_at, route_target FROM approvals "
        "WHERE status = 'pending'"
    ).fetchall()
    if len(rows) < cfg.min_count:
        return []

    aged: list[tuple[str, str, float, str]] = []
    for r in rows:
        created = datetime.fromisoformat(r["created_at"])
        age = (ctx.now - created).total_seconds()
        if age >= cfg.min_age_seconds:
            aged.append((r["request_id"], r["kind"], age, r["route_target"]))

    if not aged:
        return []

    aged.sort(key=lambda x: -x[2])
    oldest = aged[0]
    return [
        FindingCandidate(
            detector_code=CODE,
            severity=Severity.WARN,
            subject_type=SubjectType.COMPANY,
            subject_id=None,
            evidence={
                "pending_count": len(rows),
                "aged_count": len(aged),
                "oldest_request_id": oldest[0],
                "oldest_kind": oldest[1],
                "oldest_age_seconds": int(oldest[2]),
                "threshold_seconds": cfg.min_age_seconds,
            },
            recommendation=(
                f"{len(aged)} approval(s) have been pending for more than "
                f"{cfg.min_age_seconds // 60} minutes. Oldest: "
                f"{oldest[0]} ({oldest[1]}, "
                f"~{int(oldest[2]) // 60} min)."
            ),
        )
    ]
