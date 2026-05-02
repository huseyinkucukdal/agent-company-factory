"""``/companies``: create, read, pause/resume, close — proxies to Factory."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from modules.factory import (
    BootstrapFailed,
    CompanyAlreadyExists,
    CompanyNotFound,
    CompanySpec,
    CompanyStatus,
    ExtraAgentSpec,
    InvalidSpec,
)
from modules.factory import CompanySummary as _Summary
from modules.identity import Role
from modules.storage import PROJECT_WORKSPACE_ID

from ..auth.deps import (
    AuthenticatedUser,
    require_operator,
    require_user,
)
from ..deps import get_runtime
from ..rbac import UserRole, can_read_workspaces
from ..runtime import BoardRuntime
from ..schemas import (
    CompanyCreateRequest,
    CompanyDetailResponse,
    CompanyResponse,
    FileMetaResponse,
    OkResponse,
    WorkspaceFileResponse,
    WorkspaceListingResponse,
)
from ._helpers import (
    audit_record,
    conflict,
    ensure_company_access,
    not_found,
    validation_error,
)


def _summary_to_response(s: _Summary) -> CompanyResponse:
    return CompanyResponse(
        id=s.company_id,
        name=s.name,
        status=s.status.value,
        created_at=s.created_at,
        closed_at=s.closed_at,
    )


def build_companies_router() -> APIRouter:
    router = APIRouter(prefix="/companies", tags=["companies"])

    @router.get("", response_model=list[CompanyResponse])
    async def list_companies(
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> list[CompanyResponse]:
        summaries = runtime.factory.list_active()
        if current.role is UserRole.ADMIN or current.role is UserRole.OBSERVER:
            return [_summary_to_response(s) for s in summaries]
        # Operator: only their own / assigned companies.
        assigned = runtime.auth.auth.assignments(current.id)
        return [
            _summary_to_response(s) for s in summaries if s.company_id in assigned
        ] if assigned else [_summary_to_response(s) for s in summaries]

    @router.post(
        "",
        response_model=CompanyResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_company(
        body: CompanyCreateRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_operator),
    ) -> CompanyResponse:
        try:
            spec = CompanySpec(
                name=body.name,
                mission=body.mission,
                industry=body.industry,
                initial_budget_usd=Decimal(str(body.initial_budget_usd)),
                company_disk_quota_mb=body.company_disk_quota_mb,
                default_agent_quota_mb=body.default_agent_quota_mb,
                auto_approve_threshold_usd=Decimal(
                    str(body.auto_approve_threshold_usd),
                ),
                extra_agents=tuple(
                    ExtraAgentSpec(
                        role_title=ex.role_title,
                        first_name=ex.first_name,
                        last_name=ex.last_name,
                        role_description=ex.role_description,
                        persona_ref=ex.persona_ref,
                        reports_to_role=Role(ex.reports_to_role),
                    )
                    for ex in body.extra_agents
                ),
                company_id=body.company_id,
            )
        except (InvalidSpec, ValueError) as exc:
            raise validation_error(str(exc)) from exc

        try:
            handle = await runtime.factory.create_company(
                spec, requested_by=current.id,
            )
        except CompanyAlreadyExists as exc:
            raise conflict(str(exc), code="company_already_exists") from exc
        except BootstrapFailed as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": str(exc), "code": "bootstrap_failed"},
            ) from exc

        # Operator who created gets implicit assignment for visibility.
        if current.role is UserRole.OPERATOR:
            runtime.auth.auth.assign_company(current.id, handle.company_id)

        summary = runtime.factory.get_summary(handle.company_id)
        audit_record(
            runtime.audit, user_id=current.id, action="company.create",
            request=request, target=handle.company_id,
            payload={"name": spec.name, "mission": spec.mission},
        )
        return _summary_to_response(summary)

    @router.get("/{company_id}", response_model=CompanyDetailResponse)
    async def get_company(
        company_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> CompanyDetailResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            summary = runtime.factory.get_summary(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc

        clock_state: str | None = None
        company_time: datetime | None = None
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound:
            handle = None
        if handle is not None:
            clock_state = handle.clock.state().value
            company_time = handle.clock.now_company()

        return CompanyDetailResponse(
            id=summary.company_id,
            name=summary.name,
            status=summary.status.value,
            created_at=summary.created_at,
            closed_at=summary.closed_at,
            clock_state=clock_state,
            company_time=company_time,
            real_time=datetime.now(UTC),
        )

    @router.get(
        "/{company_id}/project-workspace",
        response_model=WorkspaceListingResponse,
    )
    async def list_project_workspace(
        company_id: str,
        path: str = Query(default=""),
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> WorkspaceListingResponse:
        if not can_read_workspaces(current.role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "workspace_read_forbidden", "code": "forbidden"},
            )
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        metas = handle.workspace.list(PROJECT_WORKSPACE_ID, path)
        return WorkspaceListingResponse(
            agent_id=PROJECT_WORKSPACE_ID,
            files=[
                FileMetaResponse(
                    relative_path=m.relative_path,
                    size_bytes=m.size,
                    modified_at=datetime.fromtimestamp(m.modified_at, tz=UTC),
                    is_dir=m.is_dir,
                )
                for m in metas
            ],
        )

    @router.get(
        "/{company_id}/project-workspace/file",
        response_model=WorkspaceFileResponse,
    )
    async def read_project_workspace_file(
        company_id: str,
        path: str = Query(min_length=1),
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> WorkspaceFileResponse:
        if not can_read_workspaces(current.role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "workspace_read_forbidden", "code": "forbidden"},
            )
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        try:
            data = handle.workspace.read_own(PROJECT_WORKSPACE_ID, path)
        except FileNotFoundError as exc:
            raise not_found(
                f"project_workspace_file_not_found:{path}",
                code="file_not_found",
            ) from exc
        return WorkspaceFileResponse(
            agent_id=PROJECT_WORKSPACE_ID,
            relative_path=path,
            content=data.decode("utf-8", errors="replace"),
            size_bytes=len(data),
        )

    @router.post("/{company_id}/pause", response_model=OkResponse)
    async def pause_company(
        company_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_operator),
    ) -> OkResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        await handle.clock.pause(requested_by=current.id)
        audit_record(
            runtime.audit, user_id=current.id, action="company.pause",
            request=request, target=company_id,
        )
        return OkResponse()

    @router.post("/{company_id}/resume", response_model=OkResponse)
    async def resume_company(
        company_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_operator),
    ) -> OkResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        await handle.clock.resume(requested_by=current.id)
        audit_record(
            runtime.audit, user_id=current.id, action="company.resume",
            request=request, target=company_id,
        )
        return OkResponse()

    @router.post("/{company_id}/close", response_model=OkResponse)
    async def close_company(
        company_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_operator),
    ) -> OkResponse:
        """Close + purge a company.

        The per-company SQLite DB, every agent workspace, and the
        board-level row are removed. The board audit log keeps the
        operator trail.

        Tolerates orphaned rows (board entry with no live handle, e.g.
        after an API restart): ``Factory.purge_company`` falls through
        to a disk-only scrub when ``get_handle`` raises.
        """
        ensure_company_access(runtime, current, company_id)
        try:
            await runtime.factory.purge_company(
                company_id, requested_by=current.id,
            )
        except CompanyNotFound:
            # Row vanished mid-flight (race with another operator) —
            # treat as already done.
            pass
        runtime.links.revoke_for_company(company_id, by=current.id)
        audit_record(
            runtime.audit, user_id=current.id, action="company.close",
            request=request, target=company_id,
        )
        return OkResponse()

    @router.delete("/{company_id}", response_model=OkResponse)
    async def delete_company(
        company_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_operator),
    ) -> OkResponse:
        """Idempotent purge.

        Like ``POST /close`` but always returns 200 — even if the
        company has already been closed or never existed. Used by the
        UI's "delete" affordance so a stale row never gets stuck.
        """
        ensure_company_access(runtime, current, company_id)
        try:
            await runtime.factory.purge_company(
                company_id, requested_by=current.id,
            )
        except CompanyNotFound:
            # Belt-and-suspenders: row really is gone now.
            pass
        runtime.links.revoke_for_company(company_id, by=current.id)
        audit_record(
            runtime.audit, user_id=current.id, action="company.delete",
            request=request, target=company_id,
        )
        return OkResponse()

    return router


def _ensure_active(summary: _Summary) -> None:
    if summary.status in (CompanyStatus.CLOSED, CompanyStatus.FAILED):
        raise conflict("company_closed", code="company_closed")


__all__ = ["build_companies_router"]
