"""``/audit``: admin view of every state-changing board action."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth.deps import (
    AuthenticatedUser,
    require_audit_viewer,
)
from ..deps import get_runtime
from ..runtime import BoardRuntime
from ..schemas import AuditResponse


def build_audit_router() -> APIRouter:
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("", response_model=list[AuditResponse])
    async def list_audit(
        user_id: str | None = Query(default=None),
        action: str | None = Query(default=None),
        since: int | None = Query(default=None, ge=0),
        limit: int = Query(default=200, gt=0, le=2000),
        runtime: BoardRuntime = Depends(get_runtime),
        _admin: AuthenticatedUser = Depends(require_audit_viewer),
    ) -> list[AuditResponse]:
        entries = runtime.audit.list(
            user_id=user_id, action=action, since=since, limit=limit,
        )
        return [
            AuditResponse(
                id=e.id,
                user_id=e.user_id,
                action=e.action,
                target=e.target,
                payload=e.payload,
                ts=e.ts,
                ip=e.ip,
                user_agent=e.user_agent,
            )
            for e in entries
        ]

    return router


__all__ = ["build_audit_router"]
