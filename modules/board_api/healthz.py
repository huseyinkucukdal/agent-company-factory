"""Liveness + readiness probe for Module 14 / Module 17.

The handler is intentionally cheap: a single ``SELECT 1`` against the
board DB plus a count of currently-loaded company handles. Anything more
expensive (cross-company event store probes, LLM provider checks) belongs
behind a separate ``/ready`` endpoint that operators can opt-into.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing-only import
    from .runtime import BoardRuntime


async def probe_health(runtime: "BoardRuntime") -> dict[str, Any]:
    """Return a small health envelope. Always 200 unless the DB throws."""
    db_ok = True
    db_error: str | None = None
    try:
        runtime.db.connect().execute("SELECT 1").fetchone()
    except Exception as exc:  # noqa: BLE001 - report exact failure mode
        db_ok = False
        db_error = type(exc).__name__

    active_companies = len(runtime.factory.list_active())
    loaded = sum(
        1 for _ in runtime.factory._handles  # noqa: SLF001 - cheap count
    )

    payload: dict[str, Any] = {
        "status": "ok" if db_ok else "degraded",
        "db": {"ok": db_ok},
        "companies": {
            "active": active_companies,
            "loaded": loaded,
        },
        "inter_company_wired": runtime.inter_company is not None,
    }
    if db_error is not None:
        payload["db"]["error"] = db_error
    return payload


__all__ = ["probe_health"]
