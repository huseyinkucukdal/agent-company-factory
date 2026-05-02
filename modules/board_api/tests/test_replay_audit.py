"""Replay (event log read) + audit log + agents + expenses + workspaces."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from modules.approvals import ApprovalRoute, RouteTarget
from modules.event_store import EventKind
from modules.identity import Role, Status

from .conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _create_company(
    client: httpx.AsyncClient, token: dict[str, str], spec: dict[str, Any],
) -> str:
    r = await client.post("/companies", headers=auth_headers(token), json=spec)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_replay_returns_company_events(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.get(
        f"/companies/{cid}/events", headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    events = r.json()
    assert events, "expected at least one bootstrap event"
    assert all(e["company_id"] == cid for e in events)


async def test_replay_filter_by_kind(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.get(
        f"/companies/{cid}/events",
        headers=auth_headers(admin_token),
        params=[("kinds", EventKind.AGENT_CREATED.value)],
    )
    assert r.status_code == 200
    assert all(e["kind"] == EventKind.AGENT_CREATED.value for e in r.json())


async def test_replay_unknown_kind_422(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.get(
        f"/companies/{cid}/events",
        headers=auth_headers(admin_token),
        params=[("kinds", "totally.bogus.kind")],
    )
    assert r.status_code == 422


async def test_audit_admin_only(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    observer_token: dict[str, str],
) -> None:
    r = await client.get("/audit", headers=auth_headers(admin_token))
    assert r.status_code == 200
    r2 = await client.get("/audit", headers=auth_headers(observer_token))
    assert r2.status_code == 403


async def test_audit_captures_company_create(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.get(
        "/audit", headers=auth_headers(admin_token),
        params={"action": "company.create"},
    )
    assert r.status_code == 200
    assert any(e["target"] == cid for e in r.json())


async def test_agents_endpoint_lists_bootstrap(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.get(
        f"/companies/{cid}/agents", headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    roles = sorted(a["role"] for a in r.json())
    assert "ceo" in roles and "hr" in roles and "security" in roles


async def test_fire_agent_endpoint_fires_without_board_approval(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
    factory: Any,
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    handle = factory.get_handle(cid)
    ceo_id = handle.bootstrap_agent_ids["ceo"]
    hr_id = handle.bootstrap_agent_ids["hr"]
    member = handle.identity.add_agent(
        role=Role.MEMBER,
        persona_ref="default",
        reports_to=ceo_id,
        requested_by=hr_id,
        via_hr=True,
        role_title="Engineer",
        role_description="Build product features.",
    )

    r = await client.post(
        f"/companies/{cid}/agents/{member.id}/fire",
        headers=auth_headers(admin_token),
        json={"from_agent_id": ceo_id, "reason": "role eliminated"},
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["result"] == "direct_fired"
    assert data["request_id"] is None
    assert handle.identity.get(member.id).status is Status.FIRED
    assert handle.approvals.pending_for(ApprovalRoute(target=RouteTarget.BOARD)) == []


async def test_workspace_observer_denied(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    observer_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
    factory: Any,
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    handle = factory.get_handle(cid)
    ceo_id = handle.bootstrap_agent_ids["ceo"]
    r = await client.get(
        f"/companies/{cid}/agents/{ceo_id}/workspace",
        headers=auth_headers(observer_token),
    )
    assert r.status_code == 403


async def test_workspace_admin_can_read(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
    factory: Any,
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    handle = factory.get_handle(cid)
    ceo_id = handle.bootstrap_agent_ids["ceo"]
    handle.workspace.write(ceo_id, "hello.txt", b"hi")
    r = await client.get(
        f"/companies/{cid}/agents/{ceo_id}/workspace",
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    files = r.json()["files"]
    assert any(f["relative_path"] == "hello.txt" for f in files)


async def test_workspace_admin_can_read_file_content(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    observer_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
    factory: Any,
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    handle = factory.get_handle(cid)
    ceo_id = handle.bootstrap_agent_ids["ceo"]
    handle.workspace.write(ceo_id, "notes/hello.txt", b"hello\nworld")

    denied = await client.get(
        f"/companies/{cid}/agents/{ceo_id}/workspace/file",
        params={"path": "notes/hello.txt"},
        headers=auth_headers(observer_token),
    )
    assert denied.status_code == 403

    r = await client.get(
        f"/companies/{cid}/agents/{ceo_id}/workspace/file",
        params={"path": "notes/hello.txt"},
        headers=auth_headers(admin_token),
    )

    assert r.status_code == 200
    body = r.json()
    assert body["relative_path"] == "notes/hello.txt"
    assert body["content"] == "hello\nworld"
    assert body["size_bytes"] == len(b"hello\nworld")


async def test_project_workspace_admin_can_list_and_read_file(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    observer_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
    factory: Any,
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    handle = factory.get_handle(cid)
    from modules.storage import PROJECT_WORKSPACE_ID

    handle.workspace.write(PROJECT_WORKSPACE_ID, "src/main.js", b"console.log(1)")

    denied = await client.get(
        f"/companies/{cid}/project-workspace",
        headers=auth_headers(observer_token),
    )
    assert denied.status_code == 403

    listing = await client.get(
        f"/companies/{cid}/project-workspace",
        params={"path": "src"},
        headers=auth_headers(admin_token),
    )
    assert listing.status_code == 200
    assert listing.json()["files"][0]["relative_path"] == "src/main.js"

    file = await client.get(
        f"/companies/{cid}/project-workspace/file",
        params={"path": "src/main.js"},
        headers=auth_headers(admin_token),
    )
    assert file.status_code == 200
    assert file.json()["content"] == "console.log(1)"


async def test_expenses_listing(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
    factory: Any,
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    handle = factory.get_handle(cid)
    from modules.cost import Category, Money

    handle.budget.charge(
        agent_id=None,
        amount=Money.of("1.5"),
        category=Category.OTHER,
        memo="test",
    )
    r = await client.get(
        f"/companies/{cid}/expenses", headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    assert any(e["memo"] == "test" for e in r.json())


async def test_health_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"]["ok"] is True
    assert "companies" in body
    assert "inter_company_wired" in body
