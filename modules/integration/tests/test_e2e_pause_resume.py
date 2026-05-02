"""E2E: pause/resume + restart_company round-trip.

Exercises the clock state machine through real SQLite + factory restart,
verifying that a paused company reloads in a paused-or-pausing state
after a restart and that the inter-company connector service is
re-registered for the restored handle.
"""
from __future__ import annotations

import pytest

from modules.clock import ClockState
from modules.integration import IntegratedRuntime

from .conftest import make_spec

pytestmark = pytest.mark.asyncio

_PAUSED_STATES = {ClockState.PAUSING, ClockState.PAUSED}


async def test_pause_and_restart_preserves_state(
    runtime: IntegratedRuntime,
) -> None:
    handle = await runtime.factory.create_company(
        make_spec(name="Pausable"), requested_by="USER",
    )
    company_id = handle.company_id

    await handle.clock.pause(requested_by="USER")
    # The pause finalisation depends on orchestrator drain timing; for
    # the purposes of this test, both PAUSING and PAUSED count as
    # "paused" since either persists across restart.
    assert handle.clock.state() in _PAUSED_STATES

    # Round-trip: shut down handle, drop it from the in-memory map,
    # restart from disk via the factory.
    await handle.shutdown()
    runtime.factory._handles.pop(company_id, None)  # noqa: SLF001

    restarted = await runtime.factory.restart_company(company_id)
    try:
        assert restarted.clock.state() in _PAUSED_STATES
        # Inter-company service is re-registered after restart too.
        assert restarted.connector.get_service("inter_company") is not None
    finally:
        await restarted.clock.resume(requested_by="USER")
        assert restarted.clock.state() is ClockState.RUNNING
        await restarted.shutdown()
