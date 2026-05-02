"""``/users``: admin-only user management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.deps import (
    AuthenticatedUser,
    require_user_admin,
)
from ..deps import get_runtime
from ..exceptions import EmailAlreadyExists, UserNotFound
from ..runtime import BoardRuntime
from ..schemas import (
    AssignmentRequest,
    CreateUserRequest,
    OkResponse,
    UpdateUserRequest,
    UserResponse,
)
from ._helpers import audit_record, http_error


def _user_to_response(user: object) -> UserResponse:
    # ``user`` is an ``AuthService.BoardUser``; build via attribute access so
    # this stays decoupled from the dataclass import order.
    return UserResponse(
        id=user.id,  # type: ignore[attr-defined]
        email=user.email,  # type: ignore[attr-defined]
        role=user.role,  # type: ignore[attr-defined]
        created_at=user.created_at,  # type: ignore[attr-defined]
        last_login=user.last_login,  # type: ignore[attr-defined]
    )


def build_users_router() -> APIRouter:
    router = APIRouter(prefix="/users", tags=["users"])

    @router.get("", response_model=list[UserResponse])
    async def list_users(
        runtime: BoardRuntime = Depends(get_runtime),
        _admin: AuthenticatedUser = Depends(require_user_admin),
    ) -> list[UserResponse]:
        return [_user_to_response(u) for u in runtime.auth.auth.list()]

    @router.post(
        "",
        response_model=UserResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_user(
        body: CreateUserRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        admin: AuthenticatedUser = Depends(require_user_admin),
    ) -> UserResponse:
        try:
            user = runtime.auth.auth.register(
                email=body.email, password=body.password, role=body.role,
            )
        except EmailAlreadyExists as exc:
            raise http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error": str(exc), "code": "validation_failed"},
            ) from exc
        audit_record(
            runtime.audit, user_id=admin.id, action="users.create",
            request=request, target=user.id,
            payload={"email": user.email, "role": user.role.value},
        )
        return _user_to_response(user)

    @router.get("/{user_id}", response_model=UserResponse)
    async def get_user(
        user_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        _admin: AuthenticatedUser = Depends(require_user_admin),
    ) -> UserResponse:
        try:
            return _user_to_response(runtime.auth.auth.get(user_id))
        except UserNotFound as exc:
            raise http_error(exc) from exc

    @router.patch("/{user_id}", response_model=UserResponse)
    async def update_user(
        user_id: str,
        body: UpdateUserRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        admin: AuthenticatedUser = Depends(require_user_admin),
    ) -> UserResponse:
        try:
            updated = runtime.auth.auth.set_role(user_id, body.role)
        except UserNotFound as exc:
            raise http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": str(exc), "code": "conflict"},
            ) from exc
        audit_record(
            runtime.audit, user_id=admin.id, action="users.update_role",
            request=request, target=user_id, payload={"role": body.role.value},
        )
        return _user_to_response(updated)

    @router.delete("/{user_id}", response_model=OkResponse)
    async def delete_user(
        user_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        admin: AuthenticatedUser = Depends(require_user_admin),
    ) -> OkResponse:
        if user_id == admin.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "cannot_delete_self", "code": "conflict"},
            )
        try:
            runtime.auth.auth.soft_delete(user_id)
        except UserNotFound as exc:
            raise http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": str(exc), "code": "conflict"},
            ) from exc
        audit_record(
            runtime.audit, user_id=admin.id, action="users.delete",
            request=request, target=user_id,
        )
        return OkResponse()

    @router.post(
        "/{user_id}/assignments",
        response_model=OkResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_assignment(
        user_id: str,
        body: AssignmentRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        admin: AuthenticatedUser = Depends(require_user_admin),
    ) -> OkResponse:
        try:
            runtime.auth.auth.get(user_id)
        except UserNotFound as exc:
            raise http_error(exc) from exc
        runtime.auth.auth.assign_company(user_id, body.company_id)
        audit_record(
            runtime.audit, user_id=admin.id, action="users.assign",
            request=request, target=user_id,
            payload={"company_id": body.company_id},
        )
        return OkResponse()

    @router.delete(
        "/{user_id}/assignments/{company_id}",
        response_model=OkResponse,
    )
    async def remove_assignment(
        user_id: str,
        company_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        admin: AuthenticatedUser = Depends(require_user_admin),
    ) -> OkResponse:
        runtime.auth.auth.unassign_company(user_id, company_id)
        audit_record(
            runtime.audit, user_id=admin.id, action="users.unassign",
            request=request, target=user_id,
            payload={"company_id": company_id},
        )
        return OkResponse()

    return router


__all__ = ["build_users_router"]
