"""Auth: register, login, refresh, logout, and token validation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from modules.board_api import BoardAPISettings, build_app, migrate as board_api_migrate

from .conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_register_first_user_is_admin(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/auth/register",
        json={"email": "first@boardtest.io", "password": "supersecret123"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["user"]["role"] == "admin"
    assert data["token"]["access_token"]
    assert data["token"]["refresh_token"]


async def test_second_register_defaults_to_observer(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    r = await client.post(
        "/auth/register",
        json={"email": "second@boardtest.io", "password": "anotherpass123"},
    )
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "observer"


async def test_register_duplicate_email_conflict(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    r = await client.post(
        "/auth/register",
        json={"email": "admin@boardtest.io", "password": "supersecret123"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "conflict"


async def test_login_returns_jwt(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    r = await client.post(
        "/auth/login",
        json={"email": "admin@boardtest.io", "password": "supersecret123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token"]["access_token"]
    assert body["user"]["email"] == "admin@boardtest.io"


async def test_login_invalid_credentials(client: httpx.AsyncClient, admin_token: dict[str, str]) -> None:
    r = await client.post(
        "/auth/login",
        json={"email": "admin@boardtest.io", "password": "wrongpass1"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


async def test_me_returns_current_user(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    r = await client.get("/auth/me", headers=auth_headers(admin_token))
    assert r.status_code == 200
    assert r.json()["email"] == "admin@boardtest.io"


async def test_me_requires_token(client: httpx.AsyncClient) -> None:
    r = await client.get("/auth/me")
    assert r.status_code == 401


async def test_jwt_expired_rejected(
    board_db: object, factory: object,  # type: ignore[name-defined]
) -> None:
    """Build a fresh app with a 1-second access token TTL and verify rejection."""
    short = BoardAPISettings(
        jwt_secret="short", access_token_ttl=timedelta(seconds=1),
        refresh_token_ttl=timedelta(days=1), sse_heartbeat_seconds=1,
    )
    board_api_migrate(board_db)  # type: ignore[arg-type]
    app = build_app(db=board_db, factory=factory, settings=short)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://boardtest.io",
    ) as c:
        r = await c.post(
            "/auth/register",
            json={"email": "shorttoken@boardtest.io", "password": "supersecret123"},
        )
        access = r.json()["token"]["access_token"]
        # Wait past TTL. python-jose compares int(time.time()) > int(exp), so we
        # must clear the integer-second boundary; sleep generously past TTL.
        import asyncio
        await asyncio.sleep(2.5)
        r2 = await c.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r2.status_code == 401
        assert r2.json()["code"] == "token_expired"


async def test_refresh_rotates_refresh_token(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    old_refresh = admin_token["refresh"]
    r = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200
    body = r.json()
    assert body["refresh_token"] != old_refresh
    # The previous refresh is now revoked → reuse fails.
    r2 = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401


async def test_logout_revokes_refresh(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    r = await client.post(
        "/auth/logout",
        headers=auth_headers(admin_token),
        json={"refresh_token": admin_token["refresh"]},
    )
    assert r.status_code == 200
    r2 = await client.post(
        "/auth/refresh", json={"refresh_token": admin_token["refresh"]},
    )
    assert r2.status_code == 401


async def test_invalid_token_rejected(client: httpx.AsyncClient) -> None:
    r = await client.get(
        "/auth/me", headers={"Authorization": "Bearer not.a.real.token"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "token_invalid"
