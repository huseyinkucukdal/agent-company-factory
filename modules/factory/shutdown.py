"""Graceful (and force) shutdown of a running company.

Sequence:

1. Wait for the orchestrator to report idle (bounded by ``timeout_seconds``)
   *while the clock is still running*, so in-flight messages can drain.
2. Pause the clock.
3. Stop every agent.
4. Stop the orchestrator and the clock.
5. Close the company DB and either archive or **purge** it.

``purge=True`` deletes the per-company SQLite file, every agent
workspace, and the archive directory if it still exists. The Board UI's
close button uses this so retired companies don't pile up on disk.

If the deadline is reached and ``force`` is False the function raises
:class:`ShutdownTimeout` *before* mutating any service. With ``force=True``
all stops still happen but draining is skipped.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path

from modules.event_store import EventKind

from .exceptions import ShutdownTimeout
from .handle import CompanyHandle

_log = logging.getLogger(__name__)


async def _wait_idle(handle: CompanyHandle, deadline: float) -> bool:
    loop = asyncio.get_event_loop()
    while loop.time() < deadline:
        if handle.orchestrator.is_company_idle():
            return True
        await asyncio.sleep(0.02)
    return handle.orchestrator.is_company_idle()


async def close_handle(
    handle: CompanyHandle,
    *,
    archive_root: Path,
    requested_by: str,
    timeout_seconds: float = 300.0,
    force: bool = False,
    purge: bool = False,
) -> None:
    """Drain-pause-stop-archive (or purge).

    Args:
        handle: live company handle.
        archive_root: where to drop the workspace archive when ``purge`` is
            False. Ignored when purging.
        requested_by: actor id for the closing event.
        timeout_seconds: drain budget before declaring idle-timeout.
        force: skip the idle wait and tear down regardless.
        purge: instead of archiving, delete every artifact (DB, workspace,
            archive) from disk. Use when the operator never wants to see
            this company's data again.
    """
    handle.events.append(
        EventKind.COMPANY_CLOSED,
        {"company_id": handle.company_id, "reason": "starting"},
        actor=requested_by,
    )

    if not force:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        idle = await _wait_idle(handle, deadline)
        if not idle:
            _log.warning(
                "shutdown_idle_timeout",
                extra={"company_id": handle.company_id},
            )
            raise ShutdownTimeout(
                f"company {handle.company_id} did not reach idle "
                f"within {timeout_seconds}s",
            )

    with contextlib.suppress(Exception):
        await handle.clock.pause(requested_by=requested_by)

    for agent in list(handle.agents.values()):
        with contextlib.suppress(Exception):
            await agent.stop(drain=not force)

    with contextlib.suppress(Exception):
        await handle.orchestrator.stop()

    with contextlib.suppress(Exception):
        await handle.clock.stop()

    handle.events.append(
        EventKind.COMPANY_CLOSED,
        {"company_id": handle.company_id, "reason": "complete"},
        actor=requested_by,
    )

    handle.db.close()

    if purge:
        _purge_company_artifacts(handle, archive_root)
        return

    archive_dest = archive_root / handle.company_id
    archive_dest.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError, FileExistsError):
        handle.workspace.archive_company(archive_dest)


def _purge_company_artifacts(handle: CompanyHandle, archive_root: Path) -> None:
    """Delete the per-company DB file, the workspace tree, and any archive.

    Best-effort: missing paths are silently ignored. Any other error is
    logged but does not raise — by the time we reach this function the
    handle is already torn down and the caller has nothing useful left
    to do with the failure.
    """
    db_path: Path | None = getattr(handle.db, "db_path", None)
    workspace_root: Path | None = getattr(
        handle.workspace, "root", None,
    )

    # Per-company directory: <root>/companies/<id>/ — both DB and workspaces
    # live under it on the canonical layout.
    company_dir: Path | None = None
    if db_path is not None and db_path.exists():
        company_dir = db_path.parent
    elif workspace_root is not None:
        candidate = Path(workspace_root) / "companies" / handle.company_id
        if candidate.exists():
            company_dir = candidate

    for target in (
        company_dir,
        archive_root / handle.company_id if archive_root else None,
    ):
        if target is None:
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink(missing_ok=True)
        except OSError:
            _log.exception(
                "purge_failed",
                extra={"company_id": handle.company_id, "path": str(target)},
            )


__all__ = ["close_handle"]
