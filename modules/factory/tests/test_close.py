"""close_company: graceful + force paths."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from modules.event_store import EventKind
from modules.factory import (
    CompanyFactory,
    CompanySpec,
    CompanyStatus,
)
from modules.storage import BoardDB

pytestmark = pytest.mark.asyncio


async def test_close_company_pauses_drains_and_archives(
    factory: CompanyFactory, basic_spec: CompanySpec, board_db: BoardDB,
    root: Path,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    company_id = handle.company_id

    # Capture COMPANY_CREATED events before archive moves the DB away.
    pre_close_kinds = [
        e.kind for e in handle.events.read(
            kinds=[EventKind.COMPANY_CREATED, EventKind.COMPANY_CLOSED],
        )
    ]
    assert EventKind.COMPANY_CREATED in pre_close_kinds

    await factory.close_company(company_id, requested_by="USER")

    row = board_db.connect().execute(
        "SELECT status, closed_at FROM companies WHERE id = ?",
        (company_id,),
    ).fetchone()
    assert row["status"] == CompanyStatus.CLOSED.value
    assert row["closed_at"] is not None

    # Workspace + DB archived under root/archive/<company_id>/.
    assert (root / "archive" / company_id).exists()


async def test_close_company_force_after_idle_timeout(
    factory: CompanyFactory, basic_spec: CompanySpec,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    company_id = handle.company_id

    # Pin orchestrator to "never idle" to exercise the force path.
    handle.orchestrator.is_company_idle = lambda: False  # type: ignore[method-assign]

    await factory.close_company(
        company_id,
        requested_by="USER",
        timeout_seconds=0.1,
        force_after_timeout=True,
    )

    # All agents and the orchestrator must be stopped.
    for a in handle.agents.values():
        # `_task` is None or done after `stop`.
        assert a._task is None or a._task.done()


async def test_close_company_raises_on_timeout_when_force_disabled(
    factory: CompanyFactory, basic_spec: CompanySpec,
) -> None:
    from modules.factory import ShutdownTimeout

    handle = await factory.create_company(basic_spec, requested_by="USER")
    company_id = handle.company_id
    handle.orchestrator.is_company_idle = lambda: False  # type: ignore[method-assign]

    with pytest.raises(ShutdownTimeout):
        await factory.close_company(
            company_id,
            requested_by="USER",
            timeout_seconds=0.05,
            force_after_timeout=False,
        )

    # Best-effort cleanup so the test file's other workers aren't disturbed.
    await handle.shutdown()


async def test_close_unknown_company_raises(
    factory: CompanyFactory,
) -> None:
    from modules.factory import CompanyNotFound

    with pytest.raises(CompanyNotFound):
        await factory.close_company(
            "does-not-exist", requested_by="USER",
        )


# Suppress unused-import warning when typing-only access is not used.
_ = asyncio
