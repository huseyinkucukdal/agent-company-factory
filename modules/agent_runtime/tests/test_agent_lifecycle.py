"""Agent lifecycle (start/stop/run_loop)."""
from __future__ import annotations

import asyncio

import pytest

from modules.agent_runtime import (
    Agent,
    AgentStatus,
    IncomingMessage,
    MessageKind,
)


@pytest.mark.asyncio
async def test_start_loads_persona_and_status_idle(agent: Agent) -> None:
    await agent.start()
    assert agent.status() is AgentStatus.IDLE
    await agent.stop()


@pytest.mark.asyncio
async def test_deliver_then_run_loop_processes_message(
    agent: Agent, llm: object,
) -> None:
    llm.push_text("hello")  # type: ignore[attr-defined]
    await agent.start()
    await agent.deliver(IncomingMessage(
        content="hi", kind=MessageKind.USER_REQUEST,
    ))
    # let the loop run
    for _ in range(20):
        await asyncio.sleep(0.01)
        if agent.inbox_size == 0 and agent.status() is AgentStatus.IDLE:
            break
    assert llm.run_calls  # type: ignore[attr-defined]
    await agent.stop()
    assert agent.status() is AgentStatus.STOPPED


@pytest.mark.asyncio
async def test_pause_holds_messages_until_resume(
    agent: Agent, llm: object, clock: object,
) -> None:
    llm.push_text("ok")  # type: ignore[attr-defined]
    clock.pause_sync()  # type: ignore[attr-defined]
    await agent.start()
    await agent.deliver(IncomingMessage(
        content="hi", kind=MessageKind.USER_REQUEST,
    ))
    await asyncio.sleep(0.1)
    assert not llm.run_calls  # type: ignore[attr-defined]
    assert agent.inbox_size >= 1
    clock.resume_sync()  # type: ignore[attr-defined]
    for _ in range(30):
        await asyncio.sleep(0.02)
        if llm.run_calls:  # type: ignore[attr-defined]
            break
    assert llm.run_calls  # type: ignore[attr-defined]
    await agent.stop()


@pytest.mark.asyncio
async def test_stop_drains_in_flight(agent: Agent, llm: object) -> None:
    llm.push_text("done")  # type: ignore[attr-defined]
    await agent.start()
    await agent.deliver(IncomingMessage(
        content="hi", kind=MessageKind.USER_REQUEST,
    ))
    await asyncio.sleep(0.1)
    await agent.stop(drain=True)
    assert agent.status() is AgentStatus.STOPPED
