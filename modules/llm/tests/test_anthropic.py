"""AnthropicClient: tool-use loop with the native Claude protocol."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from modules.agent_runtime import TurnEvent
from modules.llm import LLMConfigError, LLMProvider, LLMSettings
from modules.llm.anthropic_client import AnthropicClient

# ---------------------------------------------------------- fake SDK shapes


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _Resp:
    content: list[Any]
    stop_reason: str = "end_turn"


class _FakeMessages:
    def __init__(self, scripted: list[_Resp]) -> None:
        self._scripted = scripted
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("no more scripted responses")
        return self._scripted.pop(0)


class _FakeAnthropic:
    def __init__(self, scripted: list[_Resp]) -> None:
        self.messages = _FakeMessages(scripted)


def _client(settings: LLMSettings, scripted: list[_Resp]) -> AnthropicClient:
    inst = AnthropicClient.__new__(AnthropicClient)
    inst._settings = settings
    inst._client = _FakeAnthropic(scripted)
    inst._pending = {}
    return inst


def _settings() -> LLMSettings:
    return LLMSettings(
        provider=LLMProvider.ANTHROPIC,
        model="claude-3-5-haiku-latest",
        anthropic_api_key="sk-ant-test",
        max_tokens=64,
        temperature=0.0,
    )


async def _drain(stream: AsyncIterator[TurnEvent]) -> list[TurnEvent]:
    out: list[TurnEvent] = []
    async for ev in stream:
        out.append(ev)
    return out


# --------------------------------------------------------------- tests


def test_missing_key_raises() -> None:
    with pytest.raises(LLMConfigError):
        AnthropicClient(LLMSettings(provider=LLMProvider.ANTHROPIC))


async def test_text_only_turn_yields_text_then_stop() -> None:
    resp = _Resp(content=[_TextBlock(text="hello world")])
    client = _client(_settings(), [resp])
    events = await _drain(
        client.run_turn(system="sys", history=[], tool_schemas=[])
    )
    assert [e.type for e in events] == ["text", "stop"]
    assert events[0].text == "hello world"


async def test_tool_use_loop_resolves() -> None:
    round1 = _Resp(
        content=[
            _ToolUseBlock(id="tu_1", name="calc", input={"a": 2, "b": 3}),
        ],
        stop_reason="tool_use",
    )
    round2 = _Resp(content=[_TextBlock(text="answer is 5")])
    client = _client(_settings(), [round1, round2])

    stream = client.run_turn(
        system="sys", history=[], tool_schemas=[{"name": "calc"}]
    )
    iterator = stream.__aiter__()
    first = await iterator.__anext__()
    assert first.type == "tool_use"
    assert first.tool_use_id == "tu_1"
    assert first.tool_input == {"a": 2, "b": 3}

    await client.submit_tool_result(
        tool_use_id="tu_1", ok=True, output={"sum": 5}
    )

    rest = []
    async for ev in iterator:
        rest.append(ev)
    assert [e.type for e in rest] == ["text", "stop"]
    assert rest[0].text == "answer is 5"

    # Verify the tool_result block was injected into round 2.
    msgs = client._client.messages.calls[1]["messages"]
    last = msgs[-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["tool_use_id"] == "tu_1"
    assert last["content"][0]["is_error"] is False


async def test_failed_tool_result_marks_is_error() -> None:
    round1 = _Resp(
        content=[_ToolUseBlock(id="tu_1", name="calc", input={})],
        stop_reason="tool_use",
    )
    round2 = _Resp(content=[_TextBlock(text="ok")])
    client = _client(_settings(), [round1, round2])

    stream = client.run_turn(system="sys", history=[], tool_schemas=[])
    iterator = stream.__aiter__()
    await iterator.__anext__()
    await client.submit_tool_result(tool_use_id="tu_1", ok=False, output="boom")
    async for _ in iterator:
        pass

    msgs = client._client.messages.calls[1]["messages"]
    assert msgs[-1]["content"][0]["is_error"] is True
    assert msgs[-1]["content"][0]["content"] == "boom"
