"""Shared fixtures for Module 13 — Company Factory tests."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from modules.agent_runtime import LLMClient, TurnEvent
from modules.factory import (
    CompanyFactory,
    CompanySpec,
    FactoryConfig,
)
from modules.memory import WorkingItem
from modules.storage import BoardDB


class _SilentLLM:
    """Fake LLM that emits a single ``stop`` event for every turn.

    This is enough to keep agents idle right after delivery — the welcome
    message and any subsequent system events drain immediately so the
    orchestrator can report idle.
    """

    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []

    def run_turn(
        self,
        *,
        system: str,
        history: list[WorkingItem],
        tool_schemas: list[dict[str, Any]],
        cache_keys: Sequence[str] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        self.run_calls.append({"system": system, "history": list(history)})

        async def _gen() -> AsyncIterator[TurnEvent]:
            yield TurnEvent(type="stop")

        return _gen()

    async def submit_tool_result(
        self, *, tool_use_id: str, ok: bool,
        output: Mapping[str, Any] | str,
    ) -> None:
        self.tool_results.append(
            {"tool_use_id": tool_use_id, "ok": ok, "output": output},
        )

    async def completion(
        self, prompt: str, *, context: list[WorkingItem],
    ) -> str:
        return "noop"


def silent_llm_factory(_company_id: str, _agent_id: str) -> LLMClient:
    return _SilentLLM()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def board_db(root: Path) -> Iterator[BoardDB]:
    db = BoardDB.init(root)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def factory_config() -> FactoryConfig:
    return FactoryConfig(llm_factory=silent_llm_factory)


@pytest.fixture
def factory(
    board_db: BoardDB, root: Path, factory_config: FactoryConfig,
) -> CompanyFactory:
    f = CompanyFactory(board_db, root, factory_config)
    f.migrate()
    return f


@pytest.fixture
def basic_spec() -> CompanySpec:
    return CompanySpec(
        name="Acme",
        mission="Build a delightful CRM.",
        initial_budget_usd=1000,
        company_disk_quota_mb=64,
        default_agent_quota_mb=8,
        industry="SaaS",
    )
