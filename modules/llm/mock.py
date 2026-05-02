"""In-process LLM clients for tests and offline development.

Two flavours:

* :class:`MockLLMClient` \u2014 ends every turn immediately. The thing the
  development server and Module 17 CLI used to call ``_SilentLLM``.
* :class:`ScriptedLLMClient` \u2014 deterministic stream of pre-recorded
  :class:`TurnEvent`\\s, used by unit tests.

Both implement the :class:`modules.agent_runtime.LLMClient` Protocol.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from modules.agent_runtime import TurnEvent
from modules.memory import WorkingItem


class MockLLMClient:
    """Always emits a single ``stop`` event. Zero side effects.

    Suitable as a development fallback: lets the rest of the system
    (factory, board API, orchestrator) run without an LLM key.
    """

    def run_turn(
        self,
        *,
        system: str,
        history: list[WorkingItem],
        tool_schemas: list[dict[str, Any]],
        cache_keys: Sequence[str] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        async def _gen() -> AsyncIterator[TurnEvent]:
            yield TurnEvent(type="stop")

        return _gen()

    async def submit_tool_result(
        self,
        *,
        tool_use_id: str,
        ok: bool,
        output: Mapping[str, Any] | str,
    ) -> None:
        return None

    async def completion(
        self, prompt: str, *, context: list[WorkingItem]
    ) -> str:
        return ""


class ScriptedLLMClient:
    """Deterministic LLM stand-in, scripted from the test side.

    Tests push lists of :class:`TurnEvent`\\s; each ``run_turn`` call
    pops the next list. ``submit_tool_result`` calls are recorded so
    assertions can check what tool the runtime got back.
    """

    def __init__(self) -> None:
        self.scripts: list[list[TurnEvent]] = []
        self.run_calls: list[dict[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []
        self.completion_replies: list[str] = []

    def push(self, events: Sequence[TurnEvent]) -> None:
        self.scripts.append(list(events))

    def push_text(self, text: str) -> None:
        self.push(
            [TurnEvent(type="text", text=text), TurnEvent(type="stop")]
        )

    def run_turn(
        self,
        *,
        system: str,
        history: list[WorkingItem],
        tool_schemas: list[dict[str, Any]],
        cache_keys: Sequence[str] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        self.run_calls.append(
            {
                "system": system,
                "history": list(history),
                "tool_schemas": list(tool_schemas),
                "cache_keys": list(cache_keys or []),
            }
        )
        events = (
            self.scripts.pop(0) if self.scripts else [TurnEvent(type="stop")]
        )

        async def _gen() -> AsyncIterator[TurnEvent]:
            for ev in events:
                yield ev

        return _gen()

    async def submit_tool_result(
        self,
        *,
        tool_use_id: str,
        ok: bool,
        output: Mapping[str, Any] | str,
    ) -> None:
        self.tool_results.append(
            {"tool_use_id": tool_use_id, "ok": ok, "output": output}
        )

    async def completion(
        self, prompt: str, *, context: list[WorkingItem]
    ) -> str:
        if self.completion_replies:
            return self.completion_replies.pop(0)
        return ""


__all__ = ["MockLLMClient", "ScriptedLLMClient"]
