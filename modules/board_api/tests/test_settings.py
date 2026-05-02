"""Per-company settings endpoints."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest

from modules.connector import (
    ActionDef,
    AuthMethod,
    RateLimit,
    RiskLevel,
    ServiceDef,
)
from modules.cost import Money
from pydantic import BaseModel

from .conftest import auth_headers

pytestmark = pytest.mark.asyncio


class _NoArgs(BaseModel):
    pass


class _Out(BaseModel):
    ok: bool


def _make_service() -> ServiceDef:
    """A LOW-risk service with one action whose threshold we can flip."""
    async def exec_(_args: _NoArgs) -> _Out:
        return _Out(ok=True)

    def sanitize(out: _Out) -> _Out:
        return out

    return ServiceDef(
        name="testsvc",
        description="test",
        risk=RiskLevel.LOW,
        auth=AuthMethod.NONE,
        actions={
            "ping": ActionDef(
                description="ping",
                args_schema=_NoArgs,
                output_schema=_Out,
                cost_estimator=lambda _: Money.of(Decimal("0.01")),
                executor=exec_,
                sanitizer=sanitize,
                rate_limit=RateLimit(capacity=10, refill_per_second=10 / 60),
                auto_approve_threshold=Money.of(Decimal("0.10")),
                idempotent=True,
            ),
        },
    )


async def _create_company(
    client: httpx.AsyncClient, token: dict[str, str], spec: dict[str, Any],
) -> str:
    r = await client.post("/companies", headers=auth_headers(token), json=spec)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_budget_get_and_update(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.get(
        f"/companies/{cid}/budget", headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    assert Decimal(r.json()["total_usd"]) == Decimal("1000.0000")

    r2 = await client.patch(
        f"/companies/{cid}/budget",
        headers=auth_headers(admin_token),
        json={"total_usd": "1500"},
    )
    assert r2.status_code == 200
    assert Decimal(r2.json()["total_usd"]) == Decimal("1500.0000")


async def test_settings_updates_propagate(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    factory: Any,
    basic_spec_payload: dict[str, Any],
) -> None:
    """The PATCH /budget call must take effect on the live BudgetState."""
    cid = await _create_company(client, admin_token, basic_spec_payload)
    handle = factory.get_handle(cid)
    initial = handle.budget.state().total
    await client.patch(
        f"/companies/{cid}/budget",
        headers=auth_headers(admin_token),
        json={"total_usd": "5000"},
    )
    new_state = handle.budget.state()
    assert new_state.total > initial
    assert Decimal(str(new_state.total.amount_usd)) == Decimal("5000.0000")


async def test_allowlist_lifecycle(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.patch(
        f"/companies/{cid}/allowlist",
        headers=auth_headers(admin_token),
        json={
            "allow": [{"service": "http", "action": "get"}],
            "revoke": [],
        },
    )
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert any(e["service"] == "http" and e["action"] == "get" for e in entries)

    r2 = await client.patch(
        f"/companies/{cid}/allowlist",
        headers=auth_headers(admin_token),
        json={
            "allow": [],
            "revoke": [{"service": "http", "action": "get"}],
        },
    )
    assert r2.status_code == 200
    assert all(
        not (e["service"] == "http" and e["action"] == "get")
        for e in r2.json()["entries"]
    )


async def test_threshold_get_and_update(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    factory: Any,
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    handle = factory.get_handle(cid)
    handle.connector.register_service(_make_service())

    r = await client.get(
        f"/companies/{cid}/auto_approve_thresholds",
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 200
    matches = [
        t for t in r.json()["thresholds"]
        if t["service"] == "testsvc" and t["action"] == "ping"
    ]
    assert matches and matches[0]["auto_approve_threshold_usd"] == "0.1000"

    r2 = await client.patch(
        f"/companies/{cid}/auto_approve_thresholds",
        headers=auth_headers(admin_token),
        json={
            "service": "testsvc",
            "action": "ping",
            "auto_approve_threshold_usd": "0.5",
        },
    )
    assert r2.status_code == 200
    matches = [
        t for t in r2.json()["thresholds"]
        if t["service"] == "testsvc" and t["action"] == "ping"
    ]
    assert matches[0]["auto_approve_threshold_usd"] == "0.5000"

    # Connector reflects the change.
    new_def = handle.connector.get_service("testsvc").actions["ping"]
    assert new_def.auto_approve_threshold == Money.of("0.5")


async def test_threshold_unknown_action_404(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.patch(
        f"/companies/{cid}/auto_approve_thresholds",
        headers=auth_headers(admin_token),
        json={
            "service": "missing",
            "action": "x",
            "auto_approve_threshold_usd": "0.1",
        },
    )
    assert r.status_code == 404


async def test_disk_quota_company_update(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    factory: Any,
    basic_spec_payload: dict[str, Any],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    r = await client.patch(
        f"/companies/{cid}/disk_quota",
        headers=auth_headers(admin_token),
        json={"company_quota_mb": 256},
    )
    assert r.status_code == 200
    handle = factory.get_handle(cid)
    from modules.storage import CompanyQuota

    assert handle.quota.limit(CompanyQuota()) == 256 * 1024 * 1024
