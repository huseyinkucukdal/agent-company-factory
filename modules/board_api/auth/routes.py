"""HTTP routes for ``/auth/*``."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..audit import AuditLog
from ..deps import get_runtime
from ..exceptions import (
    EmailAlreadyExists,
    InvalidCredentials,
    TokenExpired,
    TokenInvalid,
    TokenRevoked,
)
from ..runtime import BoardRuntime
from ..schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    OkResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from .deps import AuthenticatedUser, require_user
from .service import BoardUser


def _user_to_response(user: BoardUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
        last_login=user.last_login,
    )


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def _audit(audit: AuditLog, *, user_id: str, action: str, request: Request, **kw: object) -> None:
    ip, ua = _client_meta(request)
    audit.record(
        user_id=user_id, action=action,
        target=str(kw.pop("target", None) or ""), payload=dict(kw),
        ip=ip, user_agent=ua,
    )


def build_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.post(
        "/register",
        response_model=LoginResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def register(
        body: RegisterRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
    ) -> LoginResponse:
        try:
            user = runtime.auth.auth.register(
                email=body.email, password=body.password,
            )
        except EmailAlreadyExists as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "email_already_exists", "code": "conflict"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error": str(exc), "code": "validation_failed"},
            ) from exc

        # Auto-login the just-registered user.
        _, pair = runtime.auth.auth.login(email=body.email, password=body.password)
        _audit(
            runtime.audit, user_id=user.id, action="auth.register",
            request=request, role=user.role.value, email=user.email,
        )
        return LoginResponse(
            user=_user_to_response(user),
            token=TokenResponse(
                access_token=pair.access_token,
                refresh_token=pair.refresh_token,
                expires_in=_ttl_seconds(pair.access_expires_at),
                refresh_expires_in=_ttl_seconds(pair.refresh_expires_at),
            ),
        )

    @router.post("/login", response_model=LoginResponse)
    async def login(
        body: LoginRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
    ) -> LoginResponse:
        try:
            user, pair = runtime.auth.auth.login(
                email=body.email, password=body.password,
            )
        except InvalidCredentials as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "invalid_credentials", "code": "unauthorized"},
            ) from exc
        _audit(
            runtime.audit, user_id=user.id, action="auth.login",
            request=request, email=user.email,
        )
        return LoginResponse(
            user=_user_to_response(user),
            token=TokenResponse(
                access_token=pair.access_token,
                refresh_token=pair.refresh_token,
                expires_in=_ttl_seconds(pair.access_expires_at),
                refresh_expires_in=_ttl_seconds(pair.refresh_expires_at),
            ),
        )

    @router.post("/refresh", response_model=TokenResponse)
    async def refresh(
        body: RefreshRequest,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
    ) -> TokenResponse:
        try:
            user, pair = runtime.auth.auth.refresh(body.refresh_token)
        except (TokenInvalid, TokenExpired, TokenRevoked, InvalidCredentials) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": exc.code, "code": "unauthorized"},
            ) from exc
        _audit(
            runtime.audit, user_id=user.id, action="auth.refresh", request=request,
        )
        return TokenResponse(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=_ttl_seconds(pair.access_expires_at),
            refresh_expires_in=_ttl_seconds(pair.refresh_expires_at),
        )

    @router.post("/logout", response_model=OkResponse)
    async def logout(
        body: LogoutRequest,
        request: Request,
        current: AuthenticatedUser = Depends(require_user),
        runtime: BoardRuntime = Depends(get_runtime),
    ) -> OkResponse:
        runtime.auth.auth.logout(body.refresh_token)
        _audit(
            runtime.audit, user_id=current.id, action="auth.logout",
            request=request,
        )
        return OkResponse()

    @router.get("/me", response_model=UserResponse)
    async def me(
        current: AuthenticatedUser = Depends(require_user),
    ) -> UserResponse:
        return _user_to_response(current.user)

    return router


def _ttl_seconds(t: object) -> int:
    """Compute integer seconds-until from a datetime in the future.

    Defensive: clamp to 0 if the datetime is already past.
    """
    from datetime import UTC, datetime

    if not isinstance(t, datetime):
        return 0
    delta = t - datetime.now(UTC)
    return max(0, int(delta.total_seconds()))


__all__ = ["build_auth_router"]
