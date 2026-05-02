"""Run every module's board-level migrations in one place.

Per-company migrations are owned by :class:`CompanyDB.migrate` and
:func:`build_company` (Module 13). This helper covers only the migrations
that target the **board** DB — everything below is idempotent and safe
to call on every boot.

Order matters: :mod:`modules.storage` defines the foundational tables
(``users``, ``companies``); other modules layer on top.
"""
from __future__ import annotations

import logging

from modules.board_api import migrate as board_api_migrate
from modules.factory import CompanyFactory
from modules.inter_company import InterCompanyService
from modules.storage import BoardDB

_log = logging.getLogger(__name__)


def migrate_all(
    *,
    board_db: BoardDB,
    factory: CompanyFactory | None = None,
    inter_company: InterCompanyService | None = None,
) -> None:
    """Apply every board-level migration.

    Parameters are optional so the CLI can run migrations before any of
    the higher-level services are constructed; tests pass already-built
    instances to avoid double-instantiation.
    """
    _log.info("migrate_all: storage")
    board_db.migrate()

    _log.info("migrate_all: board_api")
    board_api_migrate(board_db)

    if factory is not None:
        _log.info("migrate_all: factory")
        factory.migrate()

    if inter_company is not None:
        _log.info("migrate_all: inter_company")
        inter_company.migrate()


__all__ = ["migrate_all"]
