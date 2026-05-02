"""``/approvals`` and ``/companies/{id}/approvals``: pending list + decide."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from modules.approvals import (
    Approval,
    ApprovalConflict,
    ApprovalNotFound,
    ApprovalRoute,
    Decision,
    RouteTarget,
)
from modules.factory import CompanyNotFound

from ..auth.deps import (
    AuthenticatedUser,
    require_decider,
    require_user,
)
from ..deps import get_runtime
from ..rbac import UserRole
from ..runtime import BoardRuntime
from ..schemas import ApprovalDecisionRequest, ApprovalResponse
from ._helpers import audit_record, conflict, ensure_company_access, not_found


def _to_response(company_id: str, a: Approval) -> ApprovalResponse:
    return ApprovalResponse(
        request_id=a.request_id,
        company_id=company_id,
        kind=a.kind.value,
        requester_id=a.requester_id,
        payload=a.payload,
        status=a.status.value,
        route_target=a.route.encode_target(),
        require_security=a.route.require_security,
        security_decision=(
            a.security_decision.value if a.security_decision else None
        ),
        decided_by=a.decided_by,
        decided_at=a.decided_at,
        note=a.note,
        expires_at=a.expires_at,
        created_at=a.created_at,
    )


def _visible_companies(runtime: BoardRuntime, user: AuthenticatedUser) -> list[str]:
    summaries = runtime.factory.list_active()
    if user.role in (UserRole.ADMIN, UserRole.OBSERVER):
        return [s.company_id for s in summaries]
    assigned = runtime.auth.auth.assignments(user.id)
    if not assigned:
        return [s.company_id for s in summaries]
    return [s.company_id for s in summaries if s.company_id in assigned]


def build_approvals_router() -> APIRouter:
    router = APIRouter(tags=["approvals"])

    @router.get("/approvals/pending", response_model=list[ApprovalResponse])
    async def pending_for_me(
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> list[ApprovalResponse]:
        if current.role is UserRole.OBSERVER:
            return []
        out: list[ApprovalResponse] = []
        for cid in _visible_companies(runtime, current):
            try:
                handle = runtime.factory.get_handle(cid)
            except CompanyNotFound:
                continue
            board_route = ApprovalRoute(target=RouteTarget.BOARD)
            for approval in handle.approvals.pending_for(board_route):
                out.append(_to_response(cid, approval))
        return out

    @router.post(
        "/approvals/{request_id}/decide",
        response_model=ApprovalResponse,
    )
    async def decide_visible_approval(
        request_id: str,
        body: ApprovalDecisionRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_decider),
    ) -> ApprovalResponse:
        """Compatibility route for global approval cards.

        Company-scoped decide remains canonical, but older UI code and the
        cross-company inbox can decide by request id alone because request ids
        are company-local and the response includes the company id.
        """
        decision = (
            Decision.APPROVE if body.decision == "approve" else Decision.DENY
        )
        for cid in _visible_companies(runtime, current):
            try:
                handle = runtime.factory.get_handle(cid)
            except CompanyNotFound:
                continue
            try:
                handle.approvals.get(request_id)
            except ApprovalNotFound:
                continue
            try:
                updated = handle.approvals.decide(
                    request_id,
                    decider_id=current.id,
                    decision=decision,
                    note=body.note,
                )
            except ApprovalConflict as exc:
                raise conflict(str(exc), code="approval_conflict") from exc
            audit_record(
                runtime.audit,
                user_id=current.id,
                action="approval.decide",
                request=request,
                target=request_id,
                payload={"company_id": cid, "decision": body.decision},
            )
            return _to_response(cid, updated)
        raise not_found(f"approval_not_found:{request_id}", code="approval_not_found")

    @router.get(
        "/companies/{company_id}/approvals",
        response_model=list[ApprovalResponse],
    )
    async def list_company_approvals(
        company_id: str,
        status_filter: str | None = Query(default=None, alias="status"),
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> list[ApprovalResponse]:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        clauses: list[str] = []
        params: list[Any] = []
        if status_filter:
            clauses.append("status = ?")
            params.append(status_filter)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM approvals{where} ORDER BY created_at DESC LIMIT 500"
        rows = handle.db.connect().execute(sql, params).fetchall()
        out: list[ApprovalResponse] = []
        for r in rows:
            approval = handle.approvals.get(r["request_id"])
            out.append(_to_response(company_id, approval))
        return out

    @router.get(
        "/companies/{company_id}/approvals/{request_id}",
        response_model=ApprovalResponse,
    )
    async def get_approval(
        company_id: str,
        request_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> ApprovalResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        try:
            approval = handle.approvals.get(request_id)
        except ApprovalNotFound as exc:
            raise not_found(str(exc), code="approval_not_found") from exc
        return _to_response(company_id, approval)

    @router.post(
        "/companies/{company_id}/approvals/{request_id}/decide",
        response_model=ApprovalResponse,
    )
    async def decide_approval(
        company_id: str,
        request_id: str,
        body: ApprovalDecisionRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_decider),
    ) -> ApprovalResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc

        decision = (
            Decision.APPROVE if body.decision == "approve" else Decision.DENY
        )
        try:
            updated = handle.approvals.decide(
                request_id,
                decider_id=current.id,
                decision=decision,
                note=body.note,
            )
        except ApprovalNotFound as exc:
            raise not_found(str(exc), code="approval_not_found") from exc
        except ApprovalConflict as exc:
            raise conflict(str(exc), code="approval_conflict") from exc

        audit_record(
            runtime.audit, user_id=current.id, action="approval.decide",
            request=request, target=request_id,
            payload={"company_id": company_id, "decision": body.decision},
        )
        return _to_response(company_id, updated)

    return router


__all__ = ["build_approvals_router"]
