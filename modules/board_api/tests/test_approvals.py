"""Approval listing + decide + RBAC + audit trail."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from modules.approvals import ApprovalKind, ApprovalRoute, RouteTarget

from .conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _create_company(
    client: httpx.AsyncClient, token: dict[str, str], spec: dict[str, Any],
) -> str:
    r = await client.post("/companies", headers=auth_headers(token), json=spec)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _seed_pending_approval(factory: Any, company_id: str) -> str:
    handle = factory.get_handle(company_id)
    approval = handle.approvals.request(
        kind=ApprovalKind.HIRE,
        requester_id=next(iter(handle.bootstrap_agent_ids.values())),
        payload={"role": "engineer"},
        route=ApprovalRoute(target=RouteTarget.BOARD),
    )
    return approval.request_id


async def test_pending_approvals_route_correctly(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    factory: Any,
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    rid = await _seed_pending_approval(factory, cid)

    r = await client.get(
        "/approvals/pending", headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    ids = [a["request_id"] for a in r.json()]
    assert rid in ids


async def test_decide_approval_persists_and_audits(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    factory: Any,
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    rid = await _seed_pending_approval(factory, cid)

    r = await client.post(
        f"/companies/{cid}/approvals/{rid}/decide",
        headers=auth_headers(admin_token),
        json={"decision": "approve", "note": "ok"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # Audit log captured the action.
    audit_resp = await client.get(
        "/audit", headers=auth_headers(admin_token),
        params={"action": "approval.decide"},
    )
    assert audit_resp.status_code == 200
    actions = [a["target"] for a in audit_resp.json()]
    assert rid in actions


async def test_global_decide_route_finds_visible_company_approval(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    factory: Any,
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    rid = await _seed_pending_approval(factory, cid)

    r = await client.post(
        f"/approvals/{rid}/decide",
        headers=auth_headers(admin_token),
        json={"decision": "approve", "note": "ok"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["company_id"] == cid
    assert r.json()["status"] == "approved"


async def test_observer_cannot_decide(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    observer_token: dict[str, str],
    factory: Any,
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    rid = await _seed_pending_approval(factory, cid)
    r = await client.post(
        f"/companies/{cid}/approvals/{rid}/decide",
        headers=auth_headers(observer_token),
        json={"decision": "approve"},
    )
    assert r.status_code == 403


async def test_get_unknown_approval_404(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.get(
        f"/companies/{cid}/approvals/missing",
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 404


async def test_operator_decides_only_assigned(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    operator_token: dict[str, str],
    factory: Any,
    basic_spec_payload: dict[str, Any],
) -> None:
    # Two companies — operator gets assignment to only one.
    cid1 = await _create_company(client, admin_token, basic_spec_payload)
    spec2 = dict(basic_spec_payload)
    spec2["name"] = "Beta"
    cid2 = await _create_company(client, admin_token, spec2)

    rid_assigned = await _seed_pending_approval(factory, cid1)
    rid_other = await _seed_pending_approval(factory, cid2)

    # Assign the operator to cid1 only.
    r = await client.post(
        f"/users/{operator_token['user_id']}/assignments",
        headers=auth_headers(admin_token),
        json={"company_id": cid1},
    )
    assert r.status_code == 201

    # Decide assigned company → succeeds.
    r1 = await client.post(
        f"/companies/{cid1}/approvals/{rid_assigned}/decide",
        headers=auth_headers(operator_token),
        json={"decision": "approve"},
    )
    assert r1.status_code == 200

    # Decide unassigned company → forbidden.
    r2 = await client.post(
        f"/companies/{cid2}/approvals/{rid_other}/decide",
        headers=auth_headers(operator_token),
        json={"decision": "approve"},
    )
    assert r2.status_code == 403
