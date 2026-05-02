"""Company CRUD + pause/resume/close + access control."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from .conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _create_company(
    client: httpx.AsyncClient, token: dict[str, str], spec: dict[str, Any],
) -> dict[str, Any]:
    r = await client.post(
        "/companies", headers=auth_headers(token), json=spec,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_observer_cannot_create_company(
    client: httpx.AsyncClient,
    observer_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    r = await client.post(
        "/companies", headers=auth_headers(observer_token), json=basic_spec_payload,
    )
    assert r.status_code == 403


async def test_create_then_get_company(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    created = await _create_company(client, admin_token, basic_spec_payload)
    cid = created["id"]
    r = await client.get(
        f"/companies/{cid}", headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Acme"
    assert r.json()["clock_state"] == "running"


async def test_list_companies_includes_created(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    await _create_company(client, admin_token, basic_spec_payload)
    r = await client.get("/companies", headers=auth_headers(admin_token))
    assert r.status_code == 200
    names = {c["name"] for c in r.json()}
    assert "Acme" in names


async def test_pause_resume_endpoints(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    created = await _create_company(client, admin_token, basic_spec_payload)
    cid = created["id"]

    r = await client.post(
        f"/companies/{cid}/pause", headers=auth_headers(admin_token),
    )
    assert r.status_code == 200

    detail = await client.get(
        f"/companies/{cid}", headers=auth_headers(admin_token),
    )
    assert detail.json()["clock_state"] in ("pausing", "paused")

    r2 = await client.post(
        f"/companies/{cid}/resume", headers=auth_headers(admin_token),
    )
    assert r2.status_code == 200

    detail2 = await client.get(
        f"/companies/{cid}", headers=auth_headers(admin_token),
    )
    assert detail2.json()["clock_state"] == "running"


async def test_close_company_endpoint(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    created = await _create_company(client, admin_token, basic_spec_payload)
    cid = created["id"]
    r = await client.post(
        f"/companies/{cid}/close", headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    listing = await client.get("/companies", headers=auth_headers(admin_token))
    assert all(c["id"] != cid for c in listing.json())


async def test_invalid_spec_returns_422(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    r = await client.post(
        "/companies",
        headers=auth_headers(admin_token),
        json={
            "name": "",
            "mission": "x",
            "initial_budget_usd": "10",
            "company_disk_quota_mb": 1,
            "default_agent_quota_mb": 1,
        },
    )
    assert r.status_code in (422, 400)
