"""``/links``: board-managed inter-company links."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status

from ..auth.deps import (
    AuthenticatedUser,
    require_link_decider,
    require_user,
)
from ..deps import get_runtime
from ..exceptions import (
    Conflict,
    LinkAlreadyExists,
    LinkNotFound,
    NotFound,
    ValidationFailed,
)
from ..links import (
    Link,
    LinkRelationship,
    LinkScope,
    LinkStatus,
)
from ..rbac import UserRole
from ..runtime import BoardRuntime
from ..schemas import (
    LinkDecisionRequest,
    LinkRequestRequest,
    LinkResponse,
    LinkScopeModel,
    OkResponse,
)
from ._helpers import (
    audit_record,
    conflict,
    http_error,
    not_found,
    validation_error,
)


def _to_response(link: Link) -> LinkResponse:
    return LinkResponse(
        id=link.id,
        from_company=link.from_company,
        to_company=link.to_company,
        relationship=link.relationship.value,
        scope=LinkScopeModel(
            allowed_messages=list(link.scope.allowed_messages),
            rate_limit_per_hour=link.scope.rate_limit_per_hour,
            max_payload_bytes=link.scope.max_payload_bytes,
        ),
        status=link.status.value,
        requested_by=link.requested_by,
        requested_at=link.requested_at,
        decided_by=link.decided_by,
        decided_at=link.decided_at,
        note=link.note,
    )


def build_links_router() -> APIRouter:
    router = APIRouter(prefix="/links", tags=["links"])

    @router.get("", response_model=list[LinkResponse])
    async def list_links(
        company: str | None = Query(default=None),
        link_status: str | None = Query(default=None, alias="status"),
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> list[LinkResponse]:
        s = LinkStatus(link_status) if link_status else None
        scope_company = company
        if current.role is UserRole.OPERATOR:
            assigned = runtime.auth.auth.assignments(current.id)
            if scope_company is None and assigned:
                # Operator without explicit company filter: limit to assigned.
                out = []
                for link in runtime.links.list(status=s):
                    if (
                        link.from_company in assigned
                        or link.to_company in assigned
                    ):
                        out.append(_to_response(link))
                return out
        return [_to_response(link) for link in runtime.links.list(
            company=scope_company, status=s,
        )]

    @router.post(
        "",
        response_model=LinkResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def request_link(
        body: LinkRequestRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> LinkResponse:
        if current.role is UserRole.OBSERVER:
            raise http_error(
                Conflict("observer_cannot_request_link") if False else
                Conflict("observer_cannot_request_link"),
            )
        scope = LinkScope(
            allowed_messages=tuple(body.scope.allowed_messages),
            rate_limit_per_hour=body.scope.rate_limit_per_hour,
            max_payload_bytes=body.scope.max_payload_bytes,
        )
        try:
            link = runtime.links.request(
                from_company=body.from_company,
                to_company=body.to_company,
                relationship=LinkRelationship(body.relationship),
                scope=scope,
                requested_by=current.id,
            )
        except NotFound as exc:
            raise not_found(str(exc), code=exc.code) from exc
        except LinkAlreadyExists as exc:
            raise conflict(str(exc), code="link_already_exists") from exc
        except (Conflict, ValidationFailed) as exc:
            raise http_error(exc) from exc
        audit_record(
            runtime.audit, user_id=current.id, action="link.request",
            request=request, target=link.id,
            payload={
                "from_company": body.from_company,
                "to_company": body.to_company,
                "relationship": body.relationship,
            },
        )
        return _to_response(link)

    @router.get("/{link_id}", response_model=LinkResponse)
    async def get_link(
        link_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        _user: AuthenticatedUser = Depends(require_user),
    ) -> LinkResponse:
        try:
            return _to_response(runtime.links.get(link_id))
        except LinkNotFound as exc:
            raise not_found(str(exc), code="link_not_found") from exc

    @router.post("/{link_id}/decide", response_model=LinkResponse)
    async def decide_link(
        link_id: str,
        body: LinkDecisionRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        admin: AuthenticatedUser = Depends(require_link_decider),
    ) -> LinkResponse:
        try:
            link = runtime.links.decide(
                link_id, decision=body.decision, decided_by=admin.id,
                note=body.note,
            )
        except LinkNotFound as exc:
            raise not_found(str(exc), code="link_not_found") from exc
        except (Conflict, ValidationFailed) as exc:
            raise http_error(exc) from exc
        audit_record(
            runtime.audit, user_id=admin.id, action="link.decide",
            request=request, target=link_id,
            payload={"decision": body.decision},
        )
        return _to_response(link)

    @router.post("/{link_id}/suspend", response_model=LinkResponse)
    async def suspend_link(
        link_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        admin: AuthenticatedUser = Depends(require_link_decider),
    ) -> LinkResponse:
        try:
            link = runtime.links.suspend(link_id, by=admin.id, reason="manual")
        except LinkNotFound as exc:
            raise not_found(str(exc), code="link_not_found") from exc
        except Conflict as exc:
            raise http_error(exc) from exc
        audit_record(
            runtime.audit, user_id=admin.id, action="link.suspend",
            request=request, target=link_id,
        )
        return _to_response(link)

    @router.post("/{link_id}/resume", response_model=LinkResponse)
    async def resume_link(
        link_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        admin: AuthenticatedUser = Depends(require_link_decider),
    ) -> LinkResponse:
        try:
            link = runtime.links.resume(link_id, by=admin.id)
        except LinkNotFound as exc:
            raise not_found(str(exc), code="link_not_found") from exc
        except Conflict as exc:
            raise http_error(exc) from exc
        audit_record(
            runtime.audit, user_id=admin.id, action="link.resume",
            request=request, target=link_id,
        )
        return _to_response(link)

    @router.delete("/{link_id}", response_model=OkResponse)
    async def revoke_link(
        link_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        admin: AuthenticatedUser = Depends(require_link_decider),
    ) -> OkResponse:
        try:
            runtime.links.revoke(link_id, by=admin.id, reason="manual")
        except LinkNotFound as exc:
            raise not_found(str(exc), code="link_not_found") from exc
        except Conflict as exc:
            raise http_error(exc) from exc
        audit_record(
            runtime.audit, user_id=admin.id, action="link.revoke",
            request=request, target=link_id,
        )
        return OkResponse()

    return router


__all__ = ["build_links_router"]
