"""Mock and Scripted LLM clients implement the runtime contract."""
from __future__ import annotations

from collections.abc import AsyncIterator

from modules.agent_runtime import TurnEvent
from modules.llm import MockLLMClient, ScriptedLLMClient


async def _drain(stream: AsyncIterator[TurnEvent]) -> list[TurnEvent]:
    out: list[TurnEvent] = []
    async for ev in stream:
        out.append(ev)
    return out


async def test_mock_client_emits_single_stop() -> None:
    c = MockLLMClient()
    events = await _drain(
        c.run_turn(system="x", history=[], tool_schemas=[])
    )
    assert len(events) == 1
    assert events[0].type == "stop"


async def test_mock_completion_returns_empty() -> None:
    c = MockLLMClient()
    assert await c.completion("hi", context=[]) == ""


async def test_scripted_client_replays_pushed_events() -> None:
    c = ScriptedLLMClient()
    c.push_text("hello")
    events = await _drain(
        c.run_turn(system="s", history=[], tool_schemas=[])
    )
    assert [e.type for e in events] == ["text", "stop"]
    assert events[0].text == "hello"


async def test_scripted_client_records_run_calls() -> None:
    c = ScriptedLLMClient()
    c.push_text("hi")
    await _drain(
        c.run_turn(
            system="sys", history=[], tool_schemas=[{"name": "calc"}],
            cache_keys=["persona"],
        )
    )
    assert len(c.run_calls) == 1
    call = c.run_calls[0]
    assert call["system"] == "sys"
    assert call["tool_schemas"] == [{"name": "calc"}]
    assert call["cache_keys"] == ["persona"]


async def test_scripted_client_records_tool_results() -> None:
    c = ScriptedLLMClient()
    await c.submit_tool_result(
        tool_use_id="t1", ok=True, output={"answer": 42}
    )
    assert c.tool_results == [
        {"tool_use_id": "t1", "ok": True, "output": {"answer": 42}}
    ]
