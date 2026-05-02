"""Smoke tests for the ``acf`` CLI.

We invoke :func:`modules.integration.cli.main` directly so the tests
exercise the same code path as ``python -m modules.integration``
without forking a subprocess.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.integration.cli import main


def test_init_creates_board_db(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "acf"
    rc = main(["--data-dir", str(data_dir), "init"])
    assert rc == 0
    assert (data_dir / "board.db").exists()


def test_db_migrate_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "acf"
    assert main(["--data-dir", str(data_dir), "init"]) == 0
    assert main(["--data-dir", str(data_dir), "db", "migrate"]) == 0


def test_company_list_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "acf"
    main(["--data-dir", str(data_dir), "init"])
    capsys.readouterr()  # discard init output
    rc = main(["--data-dir", str(data_dir), "company", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out) == []


def test_company_create_and_describe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "acf"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps({
            "name": "CLI-Co",
            "mission": "Ship via CLI.",
            "industry": "tools",
            "initial_budget_usd": "500",
            "company_disk_quota_mb": 32,
            "default_agent_quota_mb": 4,
        }),
        encoding="utf-8",
    )
    main(["--data-dir", str(data_dir), "init"])
    capsys.readouterr()

    rc = main([
        "--data-dir", str(data_dir),
        "company", "create",
        "--spec", str(spec_path),
        "--requested-by", "tester",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    company_id = payload["company_id"]
    assert payload["status"] == "active"

    # describe should round-trip.
    rc = main([
        "--data-dir", str(data_dir),
        "company", "describe", company_id,
    ])
    assert rc == 0
    desc = json.loads(capsys.readouterr().out)
    assert desc["id"] == company_id
    assert desc["name"] == "CLI-Co"
