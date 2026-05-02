"""FastAPI application builder.

The board API is built around dependency injection so tests can wire a
fully-fledged factory without spinning up uvicorn. The module-level
``build_app`` function is the single entry point.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from modules.factory import CompanyFactory
from modules.storage import BoardDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .audit import AuditLog
from .auth.deps import AuthRuntime
from .auth.jwt import JWTCodec
from .auth.routes import build_auth_router
from .auth.service import AuthService
from .exceptions import BoardAPIError
from .links import LinkService
from .routes.agents import build_agents_router
from .routes.approvals import build_approvals_router
from .routes.audit import build_audit_router
from .routes.companies import build_companies_router
from .routes.efficiency import build_efficiency_router
from .routes.expenses import build_expenses_router
from .routes.links import build_links_router
from .routes.replay import build_replay_router
from .routes.settings import build_settings_router
from .routes.stream import build_stream_router
from .routes.users import build_users_router
from .runtime import BoardRuntime
from .settings import BoardAPISettings

if TYPE_CHECKING:  # pragma: no cover - typing-only import
    from modules.inter_company import InterCompanyService

_log = logging.getLogger(__name__)
_MODULE_KEY = "board_api"


def migrate(db: BoardDB) -> None:
    """Apply the board_api schema. Safe to call multiple times."""
    apply_migrations(
        db.connect(),
        module=_MODULE_KEY,
        migrations=load_migrations_from_package("modules.board_api.migrations"),
    )


def build_app(
    *,
    db: BoardDB,
    factory: CompanyFactory,
    settings: BoardAPISettings | None = None,
    inter_company: "InterCompanyService | None" = None,
) -> FastAPI:
    """Construct a fully-wired FastAPI application.

    Caller owns ``db`` and ``factory``: their lifecycle is independent of
    the app object so tests can keep them around across many requests.

    ``inter_company`` is optional; when supplied it is exposed via
    :attr:`BoardRuntime.inter_company` so other components (Module 17
    integration runtime, future routes) can locate it.
    """
    cfg = settings or BoardAPISettings()
    codec = JWTCodec(db, cfg)
    auth_service = AuthService(db, codec)
    audit = AuditLog(db)
    links = LinkService(db, factory)

    runtime = BoardRuntime(
        db=db,
        factory=factory,
        auth=AuthRuntime(auth=auth_service, codec=codec),
        audit=audit,
        links=links,
        inter_company=inter_company,
    )

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> Any:
        """Startup: re-attach every still-active company so a previous
        run's CEO/HR/Security come back online with the same identities.

        Without this, the board DB lists active companies but the factory
        has no in-memory handle for them, so every per-company endpoint
        404s until the operator manually closes/recreates them.

        Failures are logged and swallowed: one corrupt company should
        not prevent the API from starting at all.
        """
        try:
            handles = await factory.restart_all_active()
            if handles:
                _log.info(
                    "restarted_active_companies count=%d", len(handles),
                )
        except Exception:
            _log.exception("restart_all_active failed during startup")
        yield

    app = FastAPI(
        title="AI Company Factory — Board API",
        version="0.1.0",
        lifespan=_lifespan,
    )
    # CORS: allow the Board UI to talk to the API directly (without going
    # through Next.js dev rewrites). The dev rewrite buffers SSE frames,
    # which made the Live Feed appear silent — the UI now points its
    # EventSource at this origin directly. cors_allow_origins is a list of
    # explicit origins (no wildcard) because we send credentialed cookies.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.state.runtime = runtime
    app.state.auth = runtime.auth

    # Order matters only in OpenAPI tags; URL prefixes do not collide.
    app.include_router(build_auth_router())
    app.include_router(build_users_router())
    app.include_router(build_companies_router())
    app.include_router(build_approvals_router())
    app.include_router(build_settings_router())
    app.include_router(build_agents_router())
    app.include_router(build_expenses_router())
    app.include_router(build_replay_router())
    app.include_router(build_stream_router())
    app.include_router(build_links_router())
    app.include_router(build_audit_router())
    app.include_router(build_efficiency_router())

    @app.exception_handler(BoardAPIError)
    async def _board_error(_: Request, exc: BoardAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": str(exc) or exc.code,
                "code": exc.code,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": "validation_failed",
                "code": "validation_failed",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        # Normalise HTTPException detail into the standard envelope.
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": str(detail), "code": _http_status_to_code(exc.status_code)},
        )

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, Any]:
        from .healthz import probe_health

        return await probe_health(runtime)

    return app


def _http_status_to_code(s: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_failed",
        429: "rate_limited",
        500: "internal_error",
    }.get(s, f"http_{s}")


__all__ = ["build_app", "migrate"]
