"""Shared fixtures for Module 17 — Integration & E2E.

E2E tests use the real :func:`bootstrap_runtime` against an isolated
``tmp_path`` data dir with the same noop LLM the CLI uses. This exercises
the full wiring: storage \u2192 factory \u2192 inter_company \u2192 board API \u2192
connector services \u2192 orchestrator delivery.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from modules.agent_runtime import LLMClient, TurnEvent
from modules.factory import CompanySpec
from modules.integration import IntegratedRuntime, bootstrap_runtime
from modules.memory import WorkingItem


class _SilentLLM:
    """Same LLM stand-in used by the CLI / dev server."""

    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def run_turn(
        self, *, system: str, history: list[WorkingItem],
        tool_schemas: list[dict[str, Any]],
        cache_keys: Sequence[str] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        self.run_calls.append({"history": list(history)})

        async def _gen() -> AsyncIterator[TurnEvent]:
            yield TurnEvent(type="stop")

        return _gen()

    async def submit_tool_result(
        self, *, tool_use_id: str, ok: bool,
        output: Mapping[str, Any] | str,
    ) -> None:
        return None

    async def completion(
        self, prompt: str, *, context: list[WorkingItem],
    ) -> str:
        return ""


def _silent_factory(_company_id: str, _agent_id: str) -> LLMClient:
    return _SilentLLM()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "acf-data"
    d.mkdir()
    return d


@pytest.fixture
def runtime(data_dir: Path) -> Iterator[IntegratedRuntime]:
    rt = bootstrap_runtime(data_dir=data_dir, llm_factory=_silent_factory)
    try:
        yield rt
    finally:
        rt.close()


def make_spec(name: str = "Acme", **overrides: Any) -> CompanySpec:
    base: dict[str, Any] = {
        "name": name,
        "mission": "Build something great.",
        "industry": "SaaS",
        "initial_budget_usd": Decimal("1000"),
        "company_disk_quota_mb": 64,
        "default_agent_quota_mb": 8,
    }
    base.update(overrides)
    return CompanySpec(**base)
