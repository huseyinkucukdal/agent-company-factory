"""End-to-end runtime assembly for Module 17.

This module is the single place where every other module is composed.
``bootstrap_runtime`` creates the board DB, factory, inter-company
service, FastAPI app, and registers the inter-company connector service
for every company on create/restart.

Two construction paths exist:

* :func:`bootstrap_runtime` — opinionated, paths-and-an-llm-factory in,
  ``IntegratedRuntime`` out. Used by the CLI and the dev server.
* :func:`build_post_create_hook` — exposes the connector wiring so a
  test that already owns a factory can plug it in.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from modules.board_api import build_app
from modules.factory import CompanyFactory, CompanyHandle, FactoryConfig
from modules.factory.bootstrap import LLMFactory, PostCreateHook
from modules.inter_company import (
    InterCompanyService,
    build_inter_company_service,
)
from modules.storage import BoardDB

from .migrations import migrate_all

_log = logging.getLogger(__name__)


@dataclass
class IntegratedRuntime:
    """Everything a deployed Board needs, assembled and migrated."""

    data_dir: Path
    board_db: BoardDB
    factory: CompanyFactory
    inter_company: InterCompanyService
    app: FastAPI

    def close(self) -> None:
        """Best-effort shutdown of board-level resources.

        Active company handles are not closed here — that is the caller's
        responsibility (``acf company close`` / API).
        """
        try:
            self.board_db.close()
        except Exception:  # noqa: BLE001
            _log.exception("board_db close failed")


def register_inter_company_for_handle(
    handle: CompanyHandle, inter_company: InterCompanyService,
) -> None:
    """Register the ``inter_company`` connector service on a company.

    Idempotent: re-registering the same service name is a no-op-with-a-warning
    in the connector. If the connector rejects the registration we log and
    move on — the company is fully operational without cross-company
    messaging.
    """
    try:
        handle.connector.register_service(
            build_inter_company_service(
                from_company=handle.company_id,
                inter_company=inter_company,
            ),
        )
    except Exception:  # noqa: BLE001
        _log.exception(
            "inter_company_register_failed",
            extra={"company_id": handle.company_id},
        )


def build_post_create_hook(
    inter_company: InterCompanyService,
) -> PostCreateHook:
    """Return a hook that auto-wires inter-company on every company."""

    def _hook(handle: CompanyHandle) -> None:
        register_inter_company_for_handle(handle, inter_company)

    return _hook


def bootstrap_runtime(
    *,
    data_dir: Path,
    llm_factory: LLMFactory,
    factory_config_overrides: FactoryConfig | None = None,
) -> IntegratedRuntime:
    """Construct a fully-wired board-level runtime.

    Order:

    1. Open / create the board DB at ``<data_dir>/board.db`` and run all
       board-level migrations.
    2. Build :class:`CompanyFactory` with a ``post_create`` hook that
       registers the inter-company connector service for every fresh
       and restarted company.
    3. Build :class:`InterCompanyService` and migrate its tables.
    4. Restart any companies that were ACTIVE on the previous boot
       (``factory.restart_all_active``).
    5. Build the FastAPI app with the inter-company service injected.

    The caller owns the lifecycle of the returned runtime; nothing here
    starts a uvicorn process.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    board_db = BoardDB.init(data_dir)

    if factory_config_overrides is None:
        factory_config = FactoryConfig(llm_factory=llm_factory)
    else:
        factory_config = FactoryConfig(
            llm_factory=factory_config_overrides.llm_factory,
            persona_loader=factory_config_overrides.persona_loader,
            rate_limit=factory_config_overrides.rate_limit,
            post_create=None,  # owned by integration layer; filled below
        )

    factory = CompanyFactory(board_db, data_dir, factory_config)

    # Must run board_api migrate before LinkService touches board_links.
    migrate_all(board_db=board_db, factory=factory)

    from modules.board_api.links import LinkService
    links = LinkService(board_db, factory)

    inter_company = InterCompanyService(
        board_db=board_db, factory=factory, links=links,
    )
    inter_company.migrate()

    # CompanyFactory holds factory_config by reference; mutating
    # post_create now is enough for every subsequent create/restart.
    factory_config.post_create = build_post_create_hook(inter_company)

    app = build_app(db=board_db, factory=factory, inter_company=inter_company)

    return IntegratedRuntime(
        data_dir=data_dir,
        board_db=board_db,
        factory=factory,
        inter_company=inter_company,
        app=app,
    )


__all__ = [
    "IntegratedRuntime",
    "bootstrap_runtime",
    "build_post_create_hook",
    "register_inter_company_for_handle",
]
