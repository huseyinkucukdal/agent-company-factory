"""Failure during bootstrap rolls back DB row and on-disk artifacts."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from modules.factory import (
    BootstrapFailed,
    CompanyFactory,
    CompanySpec,
    CompanyStatus,
)
from modules.storage import BoardDB

pytestmark = pytest.mark.asyncio


async def test_bootstrap_failure_marks_failed_and_cleans_up(
    factory: CompanyFactory, basic_spec: CompanySpec, root: Path,
    board_db: BoardDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.factory import bootstrap as bootstrap_mod
    from modules.tools import register_builtins as real_register_builtins

    real_register = real_register_builtins

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated wiring failure")

    monkeypatch.setattr(bootstrap_mod, "register_builtins", boom)

    with pytest.raises(BootstrapFailed):
        await factory.create_company(basic_spec, requested_by="USER")

    # Restore so subsequent tests aren't affected (autouse safety).
    monkeypatch.setattr(bootstrap_mod, "register_builtins", real_register)

    # Board row is marked failed; no live handle exists.
    rows = board_db.connect().execute(
        "SELECT status FROM companies",
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == CompanyStatus.FAILED.value
    # On-disk dir is cleaned up.
    assert not list((root / "companies").iterdir()) or all(
        not (root / "companies" / d.name).exists()
        for d in (root / "companies").iterdir()
    )
