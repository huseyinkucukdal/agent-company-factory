"""Protocols Agent Runtime depends on (LLM client, persona loader)."""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol, runtime_checkable

from modules.identity import Role
from modules.memory import WorkingItem

from .models import TurnEvent


@runtime_checkable
class LLMClient(Protocol):
    """Adapter around the Claude Agent SDK.

    ``run_turn`` is an *async iterator* — Claude Agent SDK yields tool-use
    and text chunks until the loop terminates. After each ``tool_use`` the
    runtime calls :meth:`submit_tool_result` so the SDK loop can continue.
    """

    def run_turn(
        self,
        *,
        system: str,
        history: list[WorkingItem],
        tool_schemas: list[dict[str, Any]],
        cache_keys: list[str] | None = None,
    ) -> AsyncIterator[TurnEvent]: ...

    async def submit_tool_result(
        self, *, tool_use_id: str, ok: bool, output: Mapping[str, Any] | str,
    ) -> None: ...

    async def completion(self, prompt: str, *, context: list[WorkingItem]) -> str: ...


@runtime_checkable
class PersonaLoader(Protocol):
    def load(self, role: Role, ref: str, ctx: Mapping[str, Any]) -> str: ...


__all__ = ["LLMClient", "PersonaLoader"]
