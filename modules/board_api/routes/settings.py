"""``/companies/{id}/...`` settings: budget, allowlist, thresholds, disk quota."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status

from modules.connector import UnknownAction, UnknownService
from modules.cost import Money
from modules.factory import CompanyNotFound
from modules.storage import AgentQuota, CompanyQuota, WorkspaceNotInitialized

from ..auth.deps import (
    AuthenticatedUser,
    require_operator,
    require_user,
)
from ..deps import get_runtime
from ..runtime import BoardRuntime
from ..schemas import (
    AllowlistEntry,
    AllowlistResponse,
    AllowlistUpdateRequest,
    BudgetStateResponse,
    BudgetUpdateRequest,
    DiskQuotaUpdateRequest,
    OkResponse,
    ThresholdEntry,
    ThresholdsResponse,
    ThresholdUpdateRequest,
)
from ._helpers import audit_record, ensure_company_access, not_found, validation_error


def build_settings_router() -> APIRouter:
    router = APIRouter(prefix="/companies/{company_id}", tags=["settings"])

    # ---------------------------------------------------------------- budget

    @router.get("/budget", response_model=BudgetStateResponse)
    async def get_budget(
        company_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> BudgetStateResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        s = handle.budget.state()
        return BudgetStateResponse(
            total_usd=str(s.total.amount_usd),
            spent_usd=str(s.spent.amount_usd),
            reserved_usd=str(s.reserved.amount_usd),
            remaining_usd=str(s.remaining.amount_usd),
            blocked=s.blocked,
            by_category={
                cat.value: str(amt.amount_usd) for cat, amt in s.by_category.items()
            },
        )

    @router.patch("/budget", response_model=BudgetStateResponse)
    async def update_budget(
        company_id: str,
        body: BudgetUpdateRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_operator),
    ) -> BudgetStateResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        try:
            handle.budget.set_total(Money.of(Decimal(str(body.total_usd))))
        except ValueError as exc:
            raise validation_error(str(exc)) from exc
        audit_record(
            runtime.audit, user_id=current.id, action="budget.set_total",
            request=request, target=company_id,
            payload={"total_usd": str(body.total_usd)},
        )
        return await get_budget(  # type: ignore[no-any-return]
            company_id, runtime=runtime, current=current,
        )

    # ------------------------------------------------------------- allowlist

    @router.get("/allowlist", response_model=AllowlistResponse)
    async def get_allowlist(
        company_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> AllowlistResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        entries = [
            AllowlistEntry(service=svc, action=action)
            for svc, action in handle.connector.allowlist.entries()
        ]
        return AllowlistResponse(entries=entries)

    @router.patch("/allowlist", response_model=AllowlistResponse)
    async def update_allowlist(
        company_id: str,
        body: AllowlistUpdateRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_operator),
    ) -> AllowlistResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        for entry in body.allow:
            handle.connector.allowlist.allow(entry.service, entry.action)
        for entry in body.revoke:
            handle.connector.allowlist.revoke(entry.service, entry.action)
        audit_record(
            runtime.audit, user_id=current.id, action="allowlist.update",
            request=request, target=company_id,
            payload={
                "allow": [{"service": e.service, "action": e.action} for e in body.allow],
                "revoke": [{"service": e.service, "action": e.action} for e in body.revoke],
            },
        )
        return await get_allowlist(  # type: ignore[no-any-return]
            company_id, runtime=runtime, current=current,
        )

    # ----------------------------------------------------------- thresholds

    @router.get("/auto_approve_thresholds", response_model=ThresholdsResponse)
    async def get_thresholds(
        company_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> ThresholdsResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        return ThresholdsResponse(
            thresholds=[ThresholdEntry(**t) for t in handle.connector.list_thresholds()],
        )

    @router.patch("/auto_approve_thresholds", response_model=ThresholdsResponse)
    async def update_threshold(
        company_id: str,
        body: ThresholdUpdateRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_operator),
    ) -> ThresholdsResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        threshold: Money | None = None
        if body.auto_approve_threshold_usd is not None:
            try:
                threshold = Money.of(Decimal(str(body.auto_approve_threshold_usd)))
            except (ValueError, ArithmeticError) as exc:
                raise validation_error(str(exc)) from exc
        try:
            handle.connector.set_action_threshold(body.service, body.action, threshold)
        except (UnknownService, UnknownAction) as exc:
            raise not_found(str(exc), code="action_not_found") from exc
        audit_record(
            runtime.audit, user_id=current.id, action="threshold.update",
            request=request, target=company_id,
            payload={
                "service": body.service,
                "action": body.action,
                "threshold_usd": (
                    str(body.auto_approve_threshold_usd)
                    if body.auto_approve_threshold_usd is not None else None
                ),
            },
        )
        return await get_thresholds(  # type: ignore[no-any-return]
            company_id, runtime=runtime, current=current,
        )

    # ----------------------------------------------------------- disk quota

    @router.patch("/disk_quota", response_model=OkResponse)
    async def update_disk_quota(
        company_id: str,
        body: DiskQuotaUpdateRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_operator),
    ) -> OkResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if body.company_quota_mb is not None:
            handle.quota.set_limit(CompanyQuota(), mb=body.company_quota_mb)
        if body.agent_quota_mb:
            for agent_id, mb in body.agent_quota_mb.items():
                try:
                    handle.quota.set_limit(AgentQuota(agent_id), mb=mb)
                except WorkspaceNotInitialized as exc:
                    raise not_found(
                        f"agent_workspace_missing:{agent_id}",
                        code="agent_not_found",
                    ) from exc
        audit_record(
            runtime.audit, user_id=current.id, action="disk_quota.update",
            request=request, target=company_id,
            payload={
                "company_quota_mb": body.company_quota_mb,
                "agent_quota_mb": body.agent_quota_mb,
            },
        )
        return OkResponse()

    return router


__all__ = ["build_settings_router"]
