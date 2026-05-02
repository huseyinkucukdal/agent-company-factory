"""Module 13 — Company Factory.

Public surface: :class:`CompanyFactory` orchestrates the lifecycle of one
or more companies sharing a board-level SQLite database.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulid import ULID

from modules.storage import BoardDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .bootstrap import (
    FactoryConfig,
    LLMFactory,
    build_company,
    rollback_company_dir,
)
from .exceptions import (
    BootstrapFailed,
    CompanyAlreadyExists,
    CompanyNotFound,
)
from .handle import CompanyHandle
from .shutdown import close_handle
from .spec import CompanySpec, CompanyStatus, CompanySummary

_log = logging.getLogger(__name__)
_FACTORY_MODULE_KEY = "factory"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class CompanyFactory:
    """Create, restart and close companies.

    The factory owns:

    * the board DB row for each company,
    * the directory layout under ``root/companies/<id>/``,
    * the in-memory map of live :class:`CompanyHandle` instances.

    All async methods are safe to call concurrently for *distinct*
    company ids; per-company reentrancy is the caller's responsibility.
    """

    def __init__(
        self,
        board_db: BoardDB,
        root: Path,
        config: FactoryConfig,
    ) -> None:
        self._board_db = board_db
        self._root = Path(root)
        self._config = config
        self._handles: dict[str, CompanyHandle] = {}
        self._lock = asyncio.Lock()

    # --------------------------------------------------------- migrate

    def migrate(self) -> None:
        """Apply factory's board-level schema. Safe to call repeatedly."""
        apply_migrations(
            self._board_db.connect(),
            module=_FACTORY_MODULE_KEY,
            migrations=load_migrations_from_package(
                "modules.factory.migrations",
            ),
        )

    # --------------------------------------------------------- queries

    def list_active(self) -> list[CompanySummary]:
        rows = self._board_db.connect().execute(
            "SELECT id, name, status, created_at, closed_at "
            "FROM companies WHERE status IN (?, ?, ?) "
            "ORDER BY created_at",
            (
                CompanyStatus.CREATING.value,
                CompanyStatus.ACTIVE.value,
                CompanyStatus.CLOSING.value,
            ),
        ).fetchall()
        return [_summary_from_row(r) for r in rows]

    def get_summary(self, company_id: str) -> CompanySummary:
        row = self._board_db.connect().execute(
            "SELECT id, name, status, created_at, closed_at "
            "FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()
        if row is None:
            raise CompanyNotFound(company_id)
        return _summary_from_row(row)

    def get_handle(self, company_id: str) -> CompanyHandle:
        try:
            return self._handles[company_id]
        except KeyError as exc:
            raise CompanyNotFound(company_id) from exc

    # --------------------------------------------------------- create

    async def create_company(
        self, spec: CompanySpec, *, requested_by: str,
    ) -> CompanyHandle:
        company_id = spec.company_id or str(ULID())

        async with self._lock:
            self._reserve_row(spec, company_id, requested_by)

        try:
            handle = await build_company(
                company_id=company_id,
                spec=spec,
                root=self._root,
                requested_by=requested_by,
                config=self._config,
                send_welcome=True,
                fresh=True,
            )
        except Exception as exc:
            _log.exception(
                "bootstrap_failed",
                extra={"company_id": company_id},
            )
            self._mark_status(company_id, CompanyStatus.FAILED)
            rollback_company_dir(self._root, company_id)
            raise BootstrapFailed(str(exc)) from exc

        self._handles[company_id] = handle
        self._mark_status(company_id, CompanyStatus.ACTIVE)
        self._run_post_create(handle)
        return handle

    # --------------------------------------------------------- close

    async def close_company(
        self,
        company_id: str,
        *,
        requested_by: str,
        timeout_seconds: float = 300.0,
        force_after_timeout: bool = True,
        purge: bool = False,
    ) -> None:
        handle = self.get_handle(company_id)
        self._mark_status(company_id, CompanyStatus.CLOSING)
        archive_root = self._root / "archive"
        try:
            await close_handle(
                handle,
                archive_root=archive_root,
                requested_by=requested_by,
                timeout_seconds=timeout_seconds,
                force=False,
                purge=purge,
            )
        except Exception:
            if not force_after_timeout:
                raise
            await close_handle(
                handle,
                archive_root=archive_root,
                requested_by=requested_by,
                timeout_seconds=0.0,
                force=True,
                purge=purge,
            )
        finally:
            self._handles.pop(company_id, None)
            self._mark_status(
                company_id, CompanyStatus.CLOSED, set_closed_at=True,
            )

    async def purge_company(
        self,
        company_id: str,
        *,
        requested_by: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        """Close and **delete** a company.

        After this call the per-company DB file, every agent workspace,
        and the board-level row are gone. The board audit log still has
        the operator trail, so we never lose accountability.

        If no live handle exists (e.g. the API restarted and never
        re-attached) we fall through to :meth:`purge_orphan` so an
        operator-issued close still scrubs everything on disk.
        """
        try:
            handle = self.get_handle(company_id)
        except CompanyNotFound:
            self.purge_orphan(company_id)
            return

        del handle  # we just used it as a presence probe
        await self.close_company(
            company_id,
            requested_by=requested_by,
            timeout_seconds=timeout_seconds,
            force_after_timeout=True,
            purge=True,
        )
        with self._board_db.transaction() as conn:
            conn.execute(
                "DELETE FROM companies WHERE id = ?", (company_id,),
            )

    def purge_orphan(self, company_id: str) -> None:
        """Hard-delete a company that has no live handle.

        Used when:

        * the API restarted without re-attaching this company,
        * a previous bootstrap failed and left rubbish behind,
        * the company is in the ``CLOSING``/``CLOSED``/``FAILED`` state
          but its disk artifacts (DB file + workspaces + archive) are
          still around.

        Best-effort on disk; the board row is removed unconditionally so
        the operator never sees a phantom company they can't act on.
        """
        company_dir = self._root / "companies" / company_id
        archive_dir = self._root / "archive" / company_id
        for target in (company_dir, archive_dir):
            try:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists():
                    target.unlink(missing_ok=True)
            except OSError:
                _log.exception(
                    "purge_orphan_disk_failed",
                    extra={"company_id": company_id, "path": str(target)},
                )

        with self._board_db.transaction() as conn:
            conn.execute(
                "DELETE FROM companies WHERE id = ?", (company_id,),
            )
        self._handles.pop(company_id, None)

    # --------------------------------------------------------- restart

    async def restart_company(self, company_id: str) -> CompanyHandle:
        summary = self.get_summary(company_id)
        if summary.status not in (
            CompanyStatus.ACTIVE, CompanyStatus.CREATING,
        ):
            raise BootstrapFailed(
                f"cannot restart company in status {summary.status.value!r}",
            )
        spec = self._load_spec(company_id)
        handle = await build_company(
            company_id=company_id,
            spec=spec,
            root=self._root,
            requested_by=summary.company_id,  # actor for restart events
            config=self._config,
            send_welcome=False,
            fresh=False,
        )
        self._handles[company_id] = handle
        self._mark_status(company_id, CompanyStatus.ACTIVE)
        self._run_post_create(handle)
        return handle

    async def restart_all_active(self) -> list[CompanyHandle]:
        """Re-attach every still-active company.

        Failures on individual companies are isolated: a corrupt or
        partially-bootstrapped company gets marked ``FAILED`` and the
        loop continues. This prevents one bad row from poisoning the
        whole startup (and from spamming the logs forever via the
        efficiency loop running against an unmigrated DB).
        """
        out: list[CompanyHandle] = []
        for s in self.list_active():
            if s.status is not CompanyStatus.ACTIVE:
                continue
            try:
                out.append(await self.restart_company(s.company_id))
            except Exception:
                _log.exception(
                    "restart_company failed; marking FAILED",
                    extra={"company_id": s.company_id},
                )
                with contextlib.suppress(Exception):
                    self._mark_status(s.company_id, CompanyStatus.FAILED)
        return out

    # --------------------------------------------------------- internals

    def _reserve_row(
        self, spec: CompanySpec, company_id: str, requested_by: str,
    ) -> None:
        spec_json = json.dumps(spec.to_json(), separators=(",", ":"))
        try:
            with self._board_db.transaction() as conn:
                conn.execute(
                    "INSERT INTO companies "
                    "(id, name, mission, industry, spec_json, status, "
                    " created_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        company_id,
                        spec.name,
                        spec.mission,
                        spec.industry,
                        spec_json,
                        CompanyStatus.CREATING.value,
                        _utcnow_iso(),
                        requested_by,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise CompanyAlreadyExists(company_id) from exc

    def _mark_status(
        self, company_id: str,
        status: CompanyStatus,
        *,
        set_closed_at: bool = False,
    ) -> None:
        if set_closed_at:
            self._board_db.connect().execute(
                "UPDATE companies SET status = ?, closed_at = ? "
                "WHERE id = ?",
                (status.value, _utcnow_iso(), company_id),
            )
        else:
            self._board_db.connect().execute(
                "UPDATE companies SET status = ? WHERE id = ?",
                (status.value, company_id),
            )

    def _run_post_create(self, handle: CompanyHandle) -> None:
        hook = self._config.post_create
        if hook is None:
            return
        try:
            hook(handle)
        except Exception:  # noqa: BLE001 — hook is best-effort.
            _log.exception(
                "post_create_hook_failed",
                extra={"company_id": handle.company_id},
            )

    def _load_spec(self, company_id: str) -> CompanySpec:
        row = self._board_db.connect().execute(
            "SELECT spec_json FROM companies WHERE id = ?", (company_id,),
        ).fetchone()
        if row is None:
            raise CompanyNotFound(company_id)
        data: dict[str, Any] = json.loads(row["spec_json"])
        return _spec_from_json(data)


# ------------------------------------------------------------- helpers


def _summary_from_row(row: sqlite3.Row) -> CompanySummary:
    closed_at_raw = row["closed_at"]
    return CompanySummary(
        company_id=row["id"],
        name=row["name"],
        status=CompanyStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        closed_at=datetime.fromisoformat(closed_at_raw) if closed_at_raw else None,
    )


def _spec_from_json(data: dict[str, Any]) -> CompanySpec:
    from decimal import Decimal

    from modules.clock import ClockRate
    from modules.identity import Role

    from .spec import ExtraAgentSpec

    extras = tuple(
        ExtraAgentSpec(
            role_title=ex["role_title"],
            first_name=ex.get("first_name", ""),
            last_name=ex.get("last_name", ""),
            role_description=ex.get("role_description", ""),
            persona_ref=ex.get("persona_ref", "default"),
            reports_to_role=Role(ex["reports_to_role"]),
        )
        for ex in data.get("extra_agents", [])
    )
    rate_seconds = data.get(
        "clock_rate_seconds_per_day",
    )
    rate = (
        ClockRate(real_seconds_per_company_day=float(rate_seconds))
        if rate_seconds is not None
        else ClockRate.realtime()
    )
    return CompanySpec(
        name=data["name"],
        mission=data["mission"],
        initial_budget_usd=Decimal(str(data["initial_budget_usd"])),
        company_disk_quota_mb=int(data["company_disk_quota_mb"]),
        default_agent_quota_mb=int(data["default_agent_quota_mb"]),
        industry=data.get("industry"),
        extra_agents=extras,
        auto_approve_threshold_usd=Decimal(
            str(data.get("auto_approve_threshold_usd", "0")),
        ),
        clock_rate=rate,
        company_id=data.get("company_id"),
    )


__all__ = [
    "CompanyFactory",
    "FactoryConfig",
    "LLMFactory",
]
