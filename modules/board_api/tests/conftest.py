"""Shared fixtures for Module 14 — Board Backend API tests."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from modules.agent_runtime import LLMClient, TurnEvent
from modules.board_api import BoardAPISettings, build_app, migrate as board_api_migrate
from modules.factory import (
    CompanyFactory,
    CompanySpec,
    FactoryConfig,
)
from modules.memory import WorkingItem
from modules.storage import BoardDB


# ---------------------------------------------------------------------- llm


class _SilentLLM:
    """Fake LLM that ends every turn immediately. Mirrors factory test fake."""

    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []

    def run_turn(
        self,
        *,
        system: str,
        history: list[WorkingItem],
        tool_schemas: list[dict[str, Any]],
        cache_keys: Sequence[str] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        self.run_calls.append({"system": system, "history": list(history)})

        async def _gen() -> AsyncIterator[TurnEvent]:
            yield TurnEvent(type="stop")

        return _gen()

    async def submit_tool_result(
        self, *, tool_use_id: str, ok: bool,
        output: Mapping[str, Any] | str,
    ) -> None:
        self.tool_results.append(
            {"tool_use_id": tool_use_id, "ok": ok, "output": output},
        )

    async def completion(
        self, prompt: str, *, context: list[WorkingItem],
    ) -> str:
        return "noop"


def _silent_llm_factory(_company_id: str, _agent_id: str) -> LLMClient:
    return _SilentLLM()


# -------------------------------------------------------------------- core


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def board_db(root: Path) -> Iterator[BoardDB]:
    db = BoardDB.init(root)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def factory(board_db: BoardDB, root: Path) -> CompanyFactory:
    f = CompanyFactory(board_db, root, FactoryConfig(llm_factory=_silent_llm_factory))
    f.migrate()
    return f


@pytest.fixture
def settings() -> BoardAPISettings:
    # Long TTLs so tests don't accidentally trip on expiry; one short-TTL
    # variant is built ad-hoc in the tests that need to verify expiration.
    return BoardAPISettings(
        jwt_secret="test-secret-please-rotate",
        jwt_algorithm="HS256",
        access_token_ttl=timedelta(minutes=5),
        refresh_token_ttl=timedelta(days=1),
        sse_heartbeat_seconds=0.1,
    )


@pytest.fixture
def app(board_db: BoardDB, factory: CompanyFactory, settings: BoardAPISettings) -> Any:
    board_api_migrate(board_db)
    return build_app(db=board_db, factory=factory, settings=settings)


@pytest_asyncio.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://boardtest.io",
    ) as c:
        yield c


# --------------------------------------------------------------- helpers


@pytest_asyncio.fixture
async def admin_token(client: httpx.AsyncClient) -> dict[str, str]:
    """Register the first admin and return ``{access, refresh}`` tokens."""
    r = await client.post(
        "/auth/register",
        json={"email": "admin@boardtest.io", "password": "supersecret123"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    return {
        "access": data["token"]["access_token"],
        "refresh": data["token"]["refresh_token"],
        "user_id": data["user"]["id"],
    }


def auth_headers(token: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {token['access']}"}


@pytest_asyncio.fixture
async def operator_token(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> dict[str, str]:
    r = await client.post(
        "/users",
        headers=auth_headers(admin_token),
        json={
            "email": "op@boardtest.io",
            "password": "alsosecret123",
            "role": "operator",
        },
    )
    assert r.status_code == 201, r.text
    login = await client.post(
        "/auth/login",
        json={"email": "op@boardtest.io", "password": "alsosecret123"},
    )
    assert login.status_code == 200, login.text
    data = login.json()
    return {
        "access": data["token"]["access_token"],
        "refresh": data["token"]["refresh_token"],
        "user_id": data["user"]["id"],
    }


@pytest_asyncio.fixture
async def observer_token(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> dict[str, str]:
    r = await client.post(
        "/users",
        headers=auth_headers(admin_token),
        json={
            "email": "obs@boardtest.io",
            "password": "alsosecret123",
            "role": "observer",
        },
    )
    assert r.status_code == 201, r.text
    login = await client.post(
        "/auth/login",
        json={"email": "obs@boardtest.io", "password": "alsosecret123"},
    )
    assert login.status_code == 200, login.text
    data = login.json()
    return {
        "access": data["token"]["access_token"],
        "refresh": data["token"]["refresh_token"],
        "user_id": data["user"]["id"],
    }


@pytest.fixture
def basic_spec_payload() -> dict[str, Any]:
    return {
        "name": "Acme",
        "mission": "Build a delightful CRM.",
        "industry": "SaaS",
        "initial_budget_usd": "1000",
        "company_disk_quota_mb": 64,
        "default_agent_quota_mb": 8,
        "auto_approve_threshold_usd": "0",
        "extra_agents": [],
    }


__all__ = [
    "auth_headers",
]
