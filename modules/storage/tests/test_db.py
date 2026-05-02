"""Tests for `CompanyDB` / `BoardDB` and the migration runner."""
from __future__ import annotations

from pathlib import Path

import pytest

from modules.storage import BoardDB, CompanyDB


def test_db_init_creates_schema_version_table(storage_root: Path) -> None:
    db = CompanyDB.init("co1", storage_root)
    db.migrate()
    conn = db.connect()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "schema_version" in names
    assert "workspaces" in names
    assert "company_quota" in names
    db.close()


def test_migrate_idempotent(storage_root: Path) -> None:
    db = CompanyDB.init("co1", storage_root)
    db.migrate()
    db.migrate()  # second run must be a no-op
    conn = db.connect()
    row = conn.execute(
        "SELECT version FROM schema_version WHERE module = 'storage'"
    ).fetchone()
    assert row[0] >= 1
    db.close()


def test_board_db_migrate_creates_schema_version(storage_root: Path) -> None:
    db = BoardDB.init(storage_root)
    db.migrate()
    conn = db.connect()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    assert "schema_version" in {r[0] for r in rows}
    db.close()


def test_archive_uses_vacuum_into(storage_root: Path, tmp_path: Path) -> None:
    db = CompanyDB.init("co1", storage_root)
    db.migrate()
    dest = tmp_path / "snapshot.db"
    db.archive(dest)
    assert dest.exists()
    # The archived DB must be a valid SQLite file with our tables.
    snap = CompanyDB("co1", storage_root)
    snap.db_path = dest
    conn = snap.connect()
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "workspaces" in names
    snap.close()
    db.close()


def test_transaction_rolls_back_on_error(storage_root: Path) -> None:
    db = CompanyDB.init("co1", storage_root)
    db.migrate()
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO workspaces (agent_id, quota_bytes, used_bytes, created_at) "
            "VALUES ('a', 1, 0, '2024-01-01')"
        )
        raise RuntimeError("boom")
    conn = db.connect()
    row = conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()
    assert row[0] == 0
    db.close()
