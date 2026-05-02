"""E2E: end-to-end company creation through the integrated runtime.

Walks the full bootstrap path \u2014 board DB migrate, factory wire,
inter-company service migrate, FastAPI app build, company create, agent
bootstrap, welcome message \u2014 with the same paths a deployment would use.
"""
from __future__ import annotations

import pytest

from modules.event_store import EventKind
from modules.factory import CompanyStatus
from modules.identity import Role, Status
from modules.integration import IntegratedRuntime

from .conftest import make_spec

pytestmark = pytest.mark.asyncio


async def test_runtime_bootstraps_clean(
    runtime: IntegratedRuntime,
) -> None:
    assert runtime.app is not None
    assert runtime.factory is not None
    assert runtime.inter_company is not None
    assert runtime.app.state.runtime.inter_company is runtime.inter_company


async def test_create_company_registers_inter_company_service(
    runtime: IntegratedRuntime,
) -> None:
    handle = await runtime.factory.create_company(
        make_spec(name="Acme"), requested_by="USER",
    )
    try:
        # Bootstrap roles all present.
        roles = sorted(
            a.role.value for a in handle.identity.all(status=Status.ACTIVE)
        )
        assert roles == sorted(
            [Role.CEO.value, Role.HR.value, Role.SECURITY.value],
        )

        # Inter-company service is auto-registered via post_create hook.
        svc = handle.connector.get_service("inter_company")
        assert "send" in svc.actions

        # Company-created event is recorded.
        created = handle.events.read(kinds=[EventKind.COMPANY_CREATED])
        assert len(created) == 1
        assert created[0].payload["company_id"] == handle.company_id
    finally:
        await handle.shutdown()


async def test_replay_returns_event_history(
    runtime: IntegratedRuntime,
) -> None:
    handle = await runtime.factory.create_company(
        make_spec(name="Replayable"), requested_by="USER",
    )
    try:
        all_events = handle.events.read()
        assert len(all_events) >= 1

        # Replay slice with `until` returns a strict prefix in id order.
        first = all_events[0].id
        sliced = handle.events.read(until=first)
        assert sliced == all_events[:1]

        # And ids are monotonically increasing.
        ids = [e.id for e in all_events]
        assert ids == sorted(ids)
    finally:
        await handle.shutdown()


async def test_close_company_archives_and_marks_status(
    runtime: IntegratedRuntime,
) -> None:
    handle = await runtime.factory.create_company(
        make_spec(name="Closable"), requested_by="USER",
    )
    company_id = handle.company_id

    await runtime.factory.close_company(company_id, requested_by="USER")
    summary = runtime.factory.get_summary(company_id)
    assert summary.status is CompanyStatus.CLOSED
    assert summary.closed_at is not None
