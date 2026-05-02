"""LLMRequestCounter: persistence + increment semantics."""
from __future__ import annotations

from pathlib import Path

import pytest

from modules.llm.counter import LLMRequestCounter
from modules.storage import CompanyDB


@pytest.fixture()
def db(tmp_path: Path) -> CompanyDB:
    db = CompanyDB.init("test-cid", tmp_path)
    db.migrate()
    return db


def test_zero_state_when_no_increment(db: CompanyDB) -> None:
    c = LLMRequestCounter(db)
    c.migrate()
    s = c.read()
    assert (s.total, s.failed, s.rate_limited) == (0, 0, 0)
    assert s.last_provider is None


def test_increment_tracks_total_failed_rate_limited(db: CompanyDB) -> None:
    c = LLMRequestCounter(db)
    c.migrate()

    c.increment(ok=True, provider="openai", model="gpt-4o-mini")
    c.increment(ok=True, provider="openai", model="gpt-4o-mini")
    c.increment(ok=False, rate_limited=True, provider="openai", model="gpt-4o-mini")

    s = c.read()
    assert s.total == 3
    assert s.failed == 1
    assert s.rate_limited == 1
    assert s.last_provider == "openai"
    assert s.last_model == "gpt-4o-mini"
    assert s.last_at is not None


def test_state_survives_reopen(tmp_path: Path) -> None:
    db1 = CompanyDB.init("cid", tmp_path)
    db1.migrate()
    c1 = LLMRequestCounter(db1)
    c1.migrate()
    c1.increment(ok=True, provider="openai", model="gpt-4o-mini")
    c1.increment(ok=True, provider="openai", model="gpt-4o-mini")
    db1.close()

    db2 = CompanyDB.init("cid", tmp_path)
    c2 = LLMRequestCounter(db2)
    s = c2.read()
    assert s.total == 2
    assert s.last_provider == "openai"
