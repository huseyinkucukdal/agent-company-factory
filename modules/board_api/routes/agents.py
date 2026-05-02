"""``/companies/{id}/agents`` and workspace inspection."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from modules.factory import CompanyNotFound
from modules.identity import Status

from ..auth.deps import (
    AuthenticatedUser,
    require_decider,
    require_user,
)
from ..deps import get_runtime
from ..rbac import can_read_workspaces
from ..runtime import BoardRuntime
from ..schemas import (
    AgentResponse,
    FeedbackRequest,
    FeedbackResponse,
    FileMetaResponse,
    FireAgentRequest,
    FireAgentResponse,
    PerformanceMetricsResponse,
    PerformanceReportDeliveryResponse,
    PerformanceResponse,
    RoleChangeRequest,
    RoleChangeResponse,
    WorkspaceFileResponse,
    WorkspaceListingResponse,
)
from ._helpers import audit_record, ensure_company_access, not_found


def build_agents_router() -> APIRouter:
    router = APIRouter(
        prefix="/companies/{company_id}/agents", tags=["agents"],
    )

    @router.get("", response_model=list[AgentResponse])
    async def list_agents(
        company_id: str,
        active_only: bool = Query(default=False),
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> list[AgentResponse]:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        agents = handle.identity.all(Status.ACTIVE if active_only else None)
        return [
            AgentResponse(
                id=a.id,
                role=a.role.value,
                persona_ref=a.persona_ref,
                reports_to=a.reports_to,
                status=a.status.value,
                hired_at=a.hired_at,
                fired_at=a.fired_at,
                fired_by=a.fired_by,
                fire_reason=a.fire_reason,
                first_name=a.first_name,
                last_name=a.last_name,
                role_title=a.role_title,
                role_description=a.role_description,
            )
            for a in agents
        ]

    @router.get("/{agent_id}", response_model=AgentResponse)
    async def get_agent(
        company_id: str,
        agent_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> AgentResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        try:
            a = handle.identity.get(agent_id)
        except KeyError as exc:
            raise not_found(f"agent_not_found:{agent_id}", code="agent_not_found") from exc
        return AgentResponse(
            id=a.id,
            role=a.role.value,
            persona_ref=a.persona_ref,
            reports_to=a.reports_to,
            status=a.status.value,
            hired_at=a.hired_at,
            fired_at=a.fired_at,
            fired_by=a.fired_by,
            fire_reason=a.fire_reason,
            first_name=a.first_name,
            last_name=a.last_name,
            role_title=a.role_title,
            role_description=a.role_description,
        )

    @router.get("/{agent_id}/performance", response_model=PerformanceResponse)
    async def get_performance(
        company_id: str,
        agent_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> PerformanceResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if handle.performance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "performance_not_configured", "code": "unavailable"},
            )
        try:
            report = handle.performance.report(agent_id)
        except KeyError as exc:
            raise not_found(f"agent_not_found:{agent_id}", code="agent_not_found") from exc
        return _performance_to_response(report)

    @router.post(
        "/{agent_id}/performance/report",
        response_model=PerformanceReportDeliveryResponse,
    )
    async def send_performance_report(
        company_id: str,
        agent_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_decider),
    ) -> PerformanceReportDeliveryResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if handle.performance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "performance_not_configured", "code": "unavailable"},
            )
        try:
            delivered_to = await handle.performance.send_report_to_manager(
                target_id=agent_id,
                orchestrator=handle.orchestrator,
            )
        except KeyError as exc:
            raise not_found(f"agent_not_found:{agent_id}", code="agent_not_found") from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": str(exc), "code": "bad_request"},
            ) from exc
        audit_record(
            runtime.audit,
            user_id=current.id,
            action="agent.performance.report",
            request=request,
            target=agent_id,
            payload={"company_id": company_id, "delivered_to": delivered_to},
        )
        return PerformanceReportDeliveryResponse(delivered_to=delivered_to)

    @router.post("/{agent_id}/feedback", response_model=FeedbackResponse)
    async def give_feedback(
        company_id: str,
        agent_id: str,
        body: FeedbackRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_decider),
    ) -> FeedbackResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if handle.performance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "performance_not_configured", "code": "unavailable"},
            )
        try:
            caller_id = body.from_agent_id or _default_manager(handle, agent_id)
            feedback = handle.performance.give_feedback(
                caller_id=caller_id,
                target_id=agent_id,
                rating=body.rating,
                note=body.note,
            )
        except KeyError as exc:
            raise not_found(str(exc), code="agent_not_found") from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": str(exc), "code": "forbidden"},
            ) from exc
        audit_record(
            runtime.audit,
            user_id=current.id,
            action="agent.feedback",
            request=request,
            target=agent_id,
            payload={"company_id": company_id, "from_agent_id": feedback.from_agent_id},
        )
        return _feedback_to_response(feedback)

    @router.post("/{agent_id}/role-change", response_model=RoleChangeResponse)
    async def apply_role_change(
        company_id: str,
        agent_id: str,
        body: RoleChangeRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_decider),
    ) -> RoleChangeResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if handle.performance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "performance_not_configured", "code": "unavailable"},
            )
        try:
            caller_id = body.from_agent_id or _default_manager(handle, agent_id)
            proposal = handle.performance.propose_role_change(
                caller_id=caller_id,
                target_id=agent_id,
                new_role_title=body.new_role_title,
                new_role_description=body.new_role_description,
                rationale=body.rationale,
            )
        except KeyError as exc:
            raise not_found(str(exc), code="agent_not_found") from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": str(exc), "code": "forbidden"},
            ) from exc
        audit_record(
            runtime.audit,
            user_id=current.id,
            action="agent.role_change.apply",
            request=request,
            target=agent_id,
            payload={"company_id": company_id, "status": proposal.status},
        )
        return RoleChangeResponse(
            status=proposal.status,
            request_id=proposal.request_id,
        )

    @router.post("/{agent_id}/fire", response_model=FireAgentResponse)
    async def fire_agent(
        company_id: str,
        agent_id: str,
        body: FireAgentRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_decider),
    ) -> FireAgentResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if handle.performance is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "performance_not_configured", "code": "unavailable"},
            )
        try:
            caller_id = body.from_agent_id or _default_manager(handle, agent_id)
            outcome = handle.performance.propose_fire(
                caller_id=caller_id,
                target_id=agent_id,
                reason=body.reason,
            )
        except KeyError as exc:
            raise not_found(str(exc), code="agent_not_found") from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": str(exc), "code": "forbidden"},
            ) from exc
        audit_record(
            runtime.audit,
            user_id=current.id,
            action="agent.fire.apply",
            request=request,
            target=agent_id,
            payload={"company_id": company_id, "result": outcome.result},
        )
        return FireAgentResponse(
            result=outcome.result,
            reason=outcome.reason,
            request_id=outcome.request_id,
        )

    @router.get("/{agent_id}/workspace", response_model=WorkspaceListingResponse)
    async def list_workspace(
        company_id: str,
        agent_id: str,
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
        try:
            metas = handle.workspace.list(agent_id, path)
        except KeyError as exc:
            raise not_found(f"agent_not_found:{agent_id}", code="agent_not_found") from exc
        return WorkspaceListingResponse(
            agent_id=agent_id,
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

    @router.get("/{agent_id}/workspace/file", response_model=WorkspaceFileResponse)
    async def read_workspace_file(
        company_id: str,
        agent_id: str,
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
            data = handle.workspace.read_own(agent_id, path)
        except KeyError as exc:
            raise not_found(f"agent_not_found:{agent_id}", code="agent_not_found") from exc
        except FileNotFoundError as exc:
            raise not_found(f"workspace_file_not_found:{path}", code="file_not_found") from exc
        return WorkspaceFileResponse(
            agent_id=agent_id,
            relative_path=path,
            content=data.decode("utf-8", errors="replace"),
            size_bytes=len(data),
        )

    return router


def _feedback_to_response(feedback: object) -> FeedbackResponse:
    return FeedbackResponse(
        id=feedback.id,  # type: ignore[attr-defined]
        target_agent_id=feedback.target_agent_id,  # type: ignore[attr-defined]
        from_agent_id=feedback.from_agent_id,  # type: ignore[attr-defined]
        rating=feedback.rating,  # type: ignore[attr-defined]
        note=feedback.note,  # type: ignore[attr-defined]
        ts=feedback.ts,  # type: ignore[attr-defined]
    )


def _performance_to_response(report: object) -> PerformanceResponse:
    metrics = report.metrics  # type: ignore[attr-defined]
    return PerformanceResponse(
        metrics=PerformanceMetricsResponse(
            tasks_completed=metrics.tasks_completed,
            messages_sent=metrics.messages_sent,
            messages_received=metrics.messages_received,
            tool_success_rate=metrics.tool_success_rate,
            avg_response_seconds=metrics.avg_response_seconds,
            truncated_turns=metrics.truncated_turns,
            health_alerts=metrics.health_alerts,
            tenure_days=metrics.tenure_days,
        ),
        recent_feedback=[
            _feedback_to_response(item)
            for item in report.recent_feedback  # type: ignore[attr-defined]
        ],
    )


def _default_manager(handle: object, agent_id: str) -> str:
    manager_id = handle.identity.manager_of(agent_id)  # type: ignore[attr-defined]
    if manager_id is None:
        raise PermissionError("target_has_no_manager")
    return manager_id


__all__ = ["build_agents_router"]
