"""``/companies/{id}/events``: filtered event log read for replay UIs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from modules.event_store import EventKind
from modules.factory import CompanyNotFound
from modules.llm.counter import LLMRequestCounter

from ..auth.deps import AuthenticatedUser, require_user
from ..deps import get_runtime
from ..runtime import BoardRuntime
from ..schemas import EventResponse, LLMStatsResponse
from ._helpers import ensure_company_access, not_found, validation_error


def build_replay_router() -> APIRouter:
    router = APIRouter(prefix="/companies/{company_id}", tags=["replay"])

    @router.get("/events", response_model=list[EventResponse])
    async def read_events(
        company_id: str,
        since: int | None = Query(default=None, ge=0),
        until: int | None = Query(default=None, ge=0),
        kinds: list[str] | None = Query(default=None),
        actor: str | None = Query(default=None),
        correlation: str | None = Query(default=None),
        limit: int = Query(default=500, gt=0, le=10_000),
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> list[EventResponse]:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        try:
            kind_enums = [EventKind(k) for k in kinds] if kinds else None
        except ValueError as exc:
            raise validation_error(f"unknown_event_kind:{exc}") from exc

        events = handle.events.read(
            since=since,
            until=until,
            kinds=kind_enums,
            actor=actor,
            correlation=correlation,
            limit=limit,
        )
        return [
            EventResponse(
                id=e.id,
                company_id=e.company_id,
                kind=e.kind.value,
                payload=e.payload,
                actor_agent_id=e.actor_agent_id,
                correlation_id=e.correlation_id,
                ts_company=e.ts_company,
                ts_real=e.ts_real,
            )
            for e in events
        ]

    @router.get("/llm/stats", response_model=LLMStatsResponse)
    async def read_llm_stats(
        company_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> LLMStatsResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        stats = LLMRequestCounter(handle.db).read()
        return LLMStatsResponse(
            total=stats.total,
            failed=stats.failed,
            rate_limited=stats.rate_limited,
            last_provider=stats.last_provider,
            last_model=stats.last_model,
            last_at=stats.last_at,
        )

    return router


__all__ = ["build_replay_router"]
