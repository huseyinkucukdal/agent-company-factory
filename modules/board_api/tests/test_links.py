"""Inter-company links: request, decide, lifecycle, RBAC."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from .conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _two_companies(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> tuple[str, str]:
    a = await client.post(
        "/companies", headers=auth_headers(admin_token), json=basic_spec_payload,
    )
    spec_b = dict(basic_spec_payload)
    spec_b["name"] = "Beta"
    b = await client.post(
        "/companies", headers=auth_headers(admin_token), json=spec_b,
    )
    return a.json()["id"], b.json()["id"]


async def test_request_link_creates_pending(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    a, b = await _two_companies(client, admin_token, basic_spec_payload)
    r = await client.post(
        "/links",
        headers=auth_headers(admin_token),
        json={
            "from_company": a,
            "to_company": b,
            "relationship": "vendor",
            "scope": {
                "allowed_messages": ["inquiry", "quote"],
                "rate_limit_per_hour": 50,
                "max_payload_bytes": 1024,
            },
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "requested"


async def test_decide_link_approve(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    a, b = await _two_companies(client, admin_token, basic_spec_payload)
    create = await client.post(
        "/links",
        headers=auth_headers(admin_token),
        json={"from_company": a, "to_company": b, "relationship": "peer", "scope": {}},
    )
    link_id = create.json()["id"]
    r = await client.post(
        f"/links/{link_id}/decide",
        headers=auth_headers(admin_token),
        json={"decision": "approve", "note": "ok"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


async def test_self_link_rejected(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    a, _ = await _two_companies(client, admin_token, basic_spec_payload)
    r = await client.post(
        "/links",
        headers=auth_headers(admin_token),
        json={"from_company": a, "to_company": a, "relationship": "peer", "scope": {}},
    )
    assert r.status_code == 422


async def test_decide_unknown_link_404(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    r = await client.post(
        "/links/missing/decide",
        headers=auth_headers(admin_token),
        json={"decision": "approve"},
    )
    assert r.status_code == 404


async def test_only_admin_decides_link(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    operator_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    a, b = await _two_companies(client, admin_token, basic_spec_payload)
    create = await client.post(
        "/links",
        headers=auth_headers(admin_token),
        json={"from_company": a, "to_company": b, "relationship": "peer", "scope": {}},
    )
    link_id = create.json()["id"]
    r = await client.post(
        f"/links/{link_id}/decide",
        headers=auth_headers(operator_token),
        json={"decision": "approve"},
    )
    assert r.status_code == 403


async def test_close_company_revokes_links(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    a, b = await _two_companies(client, admin_token, basic_spec_payload)
    create = await client.post(
        "/links",
        headers=auth_headers(admin_token),
        json={"from_company": a, "to_company": b, "relationship": "peer", "scope": {}},
    )
    link_id = create.json()["id"]
    await client.post(
        f"/links/{link_id}/decide",
        headers=auth_headers(admin_token),
        json={"decision": "approve"},
    )
    await client.post(
        f"/companies/{a}/close", headers=auth_headers(admin_token),
    )
    r = await client.get(
        f"/links/{link_id}", headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "revoked"


async def test_suspend_resume_link(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    a, b = await _two_companies(client, admin_token, basic_spec_payload)
    create = await client.post(
        "/links",
        headers=auth_headers(admin_token),
        json={"from_company": a, "to_company": b, "relationship": "peer", "scope": {}},
    )
    lid = create.json()["id"]
    await client.post(
        f"/links/{lid}/decide",
        headers=auth_headers(admin_token),
        json={"decision": "approve"},
    )

    s = await client.post(f"/links/{lid}/suspend", headers=auth_headers(admin_token))
    assert s.status_code == 200
    assert s.json()["status"] == "suspended"

    r = await client.post(f"/links/{lid}/resume", headers=auth_headers(admin_token))
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
