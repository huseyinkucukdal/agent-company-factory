"""User management — admin-only CRUD plus assignments."""
from __future__ import annotations

import httpx
import pytest

from .conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_list_users_admin_only(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    observer_token: dict[str, str],
) -> None:
    r = await client.get("/users", headers=auth_headers(admin_token))
    assert r.status_code == 200
    emails = [u["email"] for u in r.json()]
    assert "admin@boardtest.io" in emails

    r2 = await client.get("/users", headers=auth_headers(observer_token))
    assert r2.status_code == 403


async def test_create_user_with_role(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    r = await client.post(
        "/users",
        headers=auth_headers(admin_token),
        json={
            "email": "marker@boardtest.io",
            "password": "longenoughpw",
            "role": "operator",
        },
    )
    assert r.status_code == 201
    assert r.json()["role"] == "operator"


async def test_update_role(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    observer_token: dict[str, str],
) -> None:
    user_id = observer_token["user_id"]
    r = await client.patch(
        f"/users/{user_id}",
        headers=auth_headers(admin_token),
        json={"role": "operator"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "operator"


async def test_cannot_demote_last_admin(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    r = await client.patch(
        f"/users/{admin_token['user_id']}",
        headers=auth_headers(admin_token),
        json={"role": "observer"},
    )
    assert r.status_code == 409


async def test_delete_user_revokes_tokens(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    observer_token: dict[str, str],
) -> None:
    r = await client.delete(
        f"/users/{observer_token['user_id']}",
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    # Refresh from observer's prior token must fail.
    r2 = await client.post(
        "/auth/refresh", json={"refresh_token": observer_token["refresh"]},
    )
    assert r2.status_code == 401


async def test_assignment_lifecycle(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    operator_token: dict[str, str],
) -> None:
    op_id = operator_token["user_id"]
    r = await client.post(
        f"/users/{op_id}/assignments",
        headers=auth_headers(admin_token),
        json={"company_id": "co_xyz"},
    )
    assert r.status_code == 201
    r2 = await client.delete(
        f"/users/{op_id}/assignments/co_xyz",
        headers=auth_headers(admin_token),
    )
    assert r2.status_code == 200
