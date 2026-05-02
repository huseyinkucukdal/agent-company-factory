"""GitHubModelsClient: tool-use loop bridge + schema translation.

The real ``openai`` SDK is not required to run these tests \u2014 we inject a
fake AsyncOpenAI-shaped client onto the instance.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from modules.agent_runtime import TurnEvent
from modules.llm import LLMConfigError, LLMProvider, LLMSettings
from modules.llm.github_models import (
    GitHubModelsClient,
    _to_openai_tools,
)

# --------------------------------------------------------------- fake SDK


@dataclass
class _FakeFunction:
    name: str
    arguments: str


@dataclass
class _FakeToolCall:
    id: str
    function: _FakeFunction
    type: str = "function"


@dataclass
class _FakeMessage:
    content: str | None = None
    tool_calls: list[_FakeToolCall] | None = None


@dataclass
class _FakeChoice:
    message: _FakeMessage
    finish_reason: str = "stop"


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice]


class _FakeCompletions:
    def __init__(self, scripted: list[_FakeResponse]) -> None:
        self._scripted = scripted
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("no more scripted responses")
        return self._scripted.pop(0)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeOpenAI:
    def __init__(self, scripted: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_FakeCompletions(scripted))


def _client(settings: LLMSettings, scripted: list[_FakeResponse]) -> GitHubModelsClient:
    # Bypass __init__ so we don't need the openai package installed.
    inst = GitHubModelsClient.__new__(GitHubModelsClient)
    inst._settings = settings
    inst._client = _FakeOpenAI(scripted)
    inst._pending = {}
    return inst


def _settings() -> LLMSettings:
    return LLMSettings(
        provider=LLMProvider.GITHUB_MODELS,
        model="gpt-4o-mini",
        github_token="ghp_test",
        max_tokens=64,
        temperature=0.0,
    )


async def _drain(stream: AsyncIterator[TurnEvent]) -> list[TurnEvent]:
    out: list[TurnEvent] = []
    async for ev in stream:
        out.append(ev)
    return out


# ----------------------------------------------------------------- tests


def test_missing_token_raises() -> None:
    with pytest.raises(LLMConfigError):
        GitHubModelsClient(LLMSettings(provider=LLMProvider.GITHUB_MODELS))


def test_to_openai_tools_translates_claude_schema() -> None:
    out = _to_openai_tools(
        [
            {
                "name": "calc",
                "description": "Add two numbers",
                "input_schema": {"type": "object"},
            }
        ]
    )
    assert out == [
        {
            "type": "function",
            "function": {
                "name": "calc",
                "description": "Add two numbers",
                "parameters": {"type": "object"},
            },
        }
    ]


async def test_text_only_turn_yields_text_then_stop() -> None:
    response = _FakeResponse(
        choices=[_FakeChoice(message=_FakeMessage(content="hi there"))]
    )
    client = _client(_settings(), [response])
    events = await _drain(
        client.run_turn(system="sys", history=[], tool_schemas=[])
    )
    assert [e.type for e in events] == ["text", "stop"]
    assert events[0].text == "hi there"


async def test_tool_use_loop_resolves_via_submit_tool_result() -> None:
    # Round 1: assistant requests a tool. Round 2: assistant emits text.
    round1 = _FakeResponse(
        choices=[
            _FakeChoice(
                message=_FakeMessage(
                    content=None,
                    tool_calls=[
                        _FakeToolCall(
                            id="call_1",
                            function=_FakeFunction(
                                name="calc",
                                arguments='{"a": 2, "b": 3}',
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ]
    )
    round2 = _FakeResponse(
        choices=[_FakeChoice(message=_FakeMessage(content="result is 5"))]
    )
    client = _client(_settings(), [round1, round2])

    stream = client.run_turn(system="sys", history=[], tool_schemas=[])
    iterator = stream.__aiter__()

    first = await iterator.__anext__()
    assert first.type == "tool_use"
    assert first.tool_name == "calc"
    assert first.tool_use_id == "call_1"
    assert first.tool_input == {"a": 2, "b": 3}

    await client.submit_tool_result(
        tool_use_id="call_1", ok=True, output={"sum": 5}
    )

    rest = []
    async for ev in iterator:
        rest.append(ev)
    assert [e.type for e in rest] == ["text", "stop"]
    assert rest[0].text == "result is 5"

    # Verify the tool message was injected into the second API call.
    second_call = client._client.chat.completions.calls[1]
    msgs = second_call["messages"]
    assert any(
        m.get("role") == "tool" and m.get("tool_call_id") == "call_1"
        for m in msgs
    )


async def test_invalid_tool_arguments_passed_through_safely() -> None:
    round1 = _FakeResponse(
        choices=[
            _FakeChoice(
                message=_FakeMessage(
                    content=None,
                    tool_calls=[
                        _FakeToolCall(
                            id="call_1",
                            function=_FakeFunction(
                                name="calc", arguments="not-json"
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ]
    )
    round2 = _FakeResponse(
        choices=[_FakeChoice(message=_FakeMessage(content="done"))]
    )
    client = _client(_settings(), [round1, round2])

    stream = client.run_turn(system="sys", history=[], tool_schemas=[])
    iterator = stream.__aiter__()
    first = await iterator.__anext__()
    assert first.tool_input == {"_raw": "not-json"}
    await client.submit_tool_result(
        tool_use_id="call_1", ok=False, output="bad args"
    )
    async for _ in iterator:
        pass


async def test_tool_loop_cap_emits_stop() -> None:
    # Force the loop to keep returning tool_calls indefinitely.
    def _looper() -> _FakeResponse:
        return _FakeResponse(
            choices=[
                _FakeChoice(
                    message=_FakeMessage(
                        content=None,
                        tool_calls=[
                            _FakeToolCall(
                                id=f"call_{id(object())}",
                                function=_FakeFunction(
                                    name="calc", arguments="{}"
                                ),
                            )
                        ],
                    )
                )
            ]
        )

    settings = _settings().model_copy(update={"max_tool_loops": 4})
    scripted = [_looper() for _ in range(settings.max_tool_loops + 2)]
    client = _client(settings, scripted)

    stream = client.run_turn(system="sys", history=[], tool_schemas=[])
    final_events: list[TurnEvent] = []
    async for ev in stream:
        if ev.type == "tool_use":
            await client.submit_tool_result(
                tool_use_id=ev.tool_use_id, ok=True, output={}
            )
        else:
            final_events.append(ev)
    assert final_events[-1].type == "stop"
    assert final_events[-1].stop_reason == "tool_loop_cap"
