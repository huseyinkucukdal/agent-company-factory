"""restart_company keeps the same agents and skips welcome."""
from __future__ import annotations

import pytest

from modules.event_store import EventKind
from modules.factory import (
    CompanyFactory,
    CompanySpec,
)
from modules.identity import Status

pytestmark = pytest.mark.asyncio


async def test_restart_resumes_with_existing_agents_and_no_welcome(
    factory: CompanyFactory, basic_spec: CompanySpec,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    company_id = handle.company_id
    pre_ids = {a.id for a in handle.identity.all(status=Status.ACTIVE)}

    # Simulate a process restart: tear down in-memory services, keep DB.
    await handle.shutdown()
    factory._handles.pop(company_id, None)

    new_handle = await factory.restart_company(company_id)
    try:
        post_ids = {
            a.id for a in new_handle.identity.all(status=Status.ACTIVE)
        }
        assert post_ids == pre_ids

        # Welcome events appear exactly once across both lifecycles.
        created = new_handle.events.read(kinds=[EventKind.COMPANY_CREATED])
        assert len(created) == 1
    finally:
        await new_handle.shutdown()
