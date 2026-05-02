"""``migrate_all`` is idempotent and creates every expected table."""
from __future__ import annotations

from pathlib import Path

import pytest

from modules.integration import bootstrap_runtime, migrate_all
from modules.storage import BoardDB

from .conftest import _silent_factory


def _tables(db: BoardDB) -> set[str]:
    rows = db.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='table'",
    ).fetchall()
    return {r[0] for r in rows}


def test_migrate_all_creates_expected_tables(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = BoardDB.init(data_dir)
    migrate_all(board_db=db)

    tables = _tables(db)
    # storage core
    assert "users" in tables
    # board_api
    assert "board_links" in tables
    assert "board_audit" in tables
    # schema_version is shared
    assert "schema_version" in tables
    db.close()


def test_migrate_all_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = BoardDB.init(data_dir)
    migrate_all(board_db=db)
    migrate_all(board_db=db)  # second call must not raise
    db.close()


def test_bootstrap_runtime_adds_factory_and_inter_company_tables(
    tmp_path: Path,
) -> None:
    runtime = bootstrap_runtime(
        data_dir=tmp_path / "data", llm_factory=_silent_factory,
    )
    try:
        tables = _tables(runtime.board_db)
        assert "inter_company_outbox" in tables
        # factory's company registry is part of storage migrations,
        # already covered above; just sanity check.
        assert "companies" in tables
    finally:
        runtime.close()
