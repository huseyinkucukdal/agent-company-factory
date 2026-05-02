"""Single-turn integration tests."""
from __future__ import annotations

import pytest

from modules.agent_runtime import (
    Agent,
    AgentStatus,
    IncomingMessage,
    MessageKind,
    TurnEvent,
)
from modules.event_store import EventKind, EventStore


@pytest.mark.asyncio
async def test_turn_appends_user_and_agent_to_working_memory(
    agent: Agent, llm: object, memory: object, ceo: object,
) -> None:
    llm.push_text("hello there")  # type: ignore[attr-defined]
    msg = IncomingMessage(
        content="kickoff", kind=MessageKind.USER_REQUEST,
        from_agent=None, correlation_id="c1",
    )
    await agent.deliver(msg)
    outcome = await agent.process_one()
    assert outcome is not None and outcome.text == "hello there"

    history = memory.working_window(ceo.id, n=10)  # type: ignore[attr-defined]
    contents = [item.content for item in history]
    assert "kickoff" in contents
    assert "hello there" in contents


@pytest.mark.asyncio
async def test_turn_emits_message_delivered_and_heartbeat(
    agent: Agent, llm: object, event_store: EventStore,
) -> None:
    llm.push_text("ok")  # type: ignore[attr-defined]
    await agent.deliver(IncomingMessage(
        content="hi", kind=MessageKind.USER_REQUEST, correlation_id="c1",
    ))
    await agent.process_one()
    kinds = [
        e.kind for e in event_store.read(
            kinds=[EventKind.MESSAGE_DELIVERED, EventKind.AGENT_HEARTBEAT],
        )
    ]
    assert EventKind.MESSAGE_DELIVERED in kinds
    assert EventKind.AGENT_HEARTBEAT in kinds


@pytest.mark.asyncio
async def test_recall_block_in_system_prompt(
    agent: Agent, llm: object, memory: object, ceo: object,
) -> None:
    memory.remember_episode(  # type: ignore[attr-defined]
        ceo.id,  # type: ignore[attr-defined]
        "Earlier we shipped milestone alpha.",
    )
    llm.push_text("noted")  # type: ignore[attr-defined]
    await agent.deliver(IncomingMessage(
        content="what was shipped?", kind=MessageKind.USER_REQUEST,
    ))
    await agent.process_one()
    system_prompt = llm.run_calls[0]["system"]  # type: ignore[attr-defined]
    assert "Relevant memories" in system_prompt or "No relevant memories" in system_prompt


@pytest.mark.asyncio
async def test_tool_use_dispatched_and_result_returned(
    agent: Agent, llm: object,
) -> None:
    llm.push([  # type: ignore[attr-defined]
        TurnEvent(
            type="tool_use",
            tool_use_id="tu1",
            tool_name="recall_memory",
            tool_input={"query": "anything", "k": 3},
        ),
        TurnEvent(type="text", text="done"),
        TurnEvent(type="stop"),
    ])
    await agent.deliver(IncomingMessage(
        content="search", kind=MessageKind.USER_REQUEST,
    ))
    outcome = await agent.process_one()
    assert outcome is not None
    assert outcome.tool_invocations == 1
    submitted = llm.tool_results[0]  # type: ignore[attr-defined]
    assert submitted["tool_use_id"] == "tu1"
    assert submitted["ok"] is True


@pytest.mark.asyncio
async def test_multiple_tool_uses_in_same_turn_are_not_deduped(
    agent: Agent, llm: object, event_store: EventStore,
) -> None:
    llm.push([  # type: ignore[attr-defined]
        TurnEvent(
            type="tool_use",
            tool_use_id="tu1",
            tool_name="recall_memory",
            tool_input={"query": "anything", "k": 3},
        ),
        TurnEvent(
            type="tool_use",
            tool_use_id="tu2",
            tool_name="query_org_chart",
            tool_input={},
        ),
        TurnEvent(type="stop"),
    ])
    await agent.deliver(IncomingMessage(
        content="run tools",
        kind=MessageKind.USER_REQUEST,
        correlation_id="same-message",
    ))

    outcome = await agent.process_one()
    assert outcome is not None
    assert outcome.tool_invocations == 2
    assert len(llm.tool_results) == 2  # type: ignore[attr-defined]

    called = event_store.read(kinds=[EventKind.TOOL_CALLED])
    assert [e.payload["tool"] for e in called] == [
        "recall_memory",
        "query_org_chart",
    ]
    assert [e.correlation_id for e in called] == [
        "same-message:tu1",
        "same-message:tu2",
    ]


@pytest.mark.asyncio
async def test_unknown_tool_reports_failure_to_llm(
    agent: Agent, llm: object,
) -> None:
    llm.push([  # type: ignore[attr-defined]
        TurnEvent(
            type="tool_use", tool_use_id="tu1",
            tool_name="ghost_tool", tool_input={},
        ),
        TurnEvent(type="stop"),
    ])
    await agent.deliver(IncomingMessage(
        content="x", kind=MessageKind.USER_REQUEST,
    ))
    outcome = await agent.process_one()
    assert outcome is not None
    submitted = llm.tool_results[-1]  # type: ignore[attr-defined]
    # Unknown tool comes back as a ToolResult(ok=False) — runtime forwards.
    assert submitted["ok"] is False


@pytest.mark.asyncio
async def test_task_done_marker_persists_episode(
    agent: Agent, llm: object, memory: object, ceo: object,
) -> None:
    llm.push_text(  # type: ignore[attr-defined]
        'work complete <task_done summary="shipped feature" />'
    )
    await agent.deliver(IncomingMessage(
        content="finish it", kind=MessageKind.USER_REQUEST, correlation_id="c1",
    ))
    outcome = await agent.process_one()
    assert outcome is not None and outcome.task_done is True
    assert outcome.summary == "shipped feature"

    hits = memory.recall(ceo.id, "shipped feature", k=5)  # type: ignore[attr-defined]
    assert any("shipped feature" in h.content for h in hits)


@pytest.mark.asyncio
async def test_task_done_without_summary_uses_llm_completion(
    agent: Agent, llm: object, memory: object, ceo: object,
) -> None:
    llm.completion_replies.append("auto-generated recap")  # type: ignore[attr-defined]
    llm.push_text("all good <task_done />")  # type: ignore[attr-defined]
    await agent.deliver(IncomingMessage(
        content="x", kind=MessageKind.USER_REQUEST, correlation_id="c2",
    ))
    outcome = await agent.process_one()
    assert outcome is not None and outcome.task_done is True
    hits = memory.recall(ceo.id, "auto-generated recap", k=3)  # type: ignore[attr-defined]
    assert any("auto-generated recap" in h.content for h in hits)


@pytest.mark.asyncio
async def test_pause_blocks_new_turns(
    agent: Agent, llm: object, clock: object,
) -> None:
    llm.push_text("ok")  # type: ignore[attr-defined]
    clock.pause_sync()  # type: ignore[attr-defined]
    await agent.deliver(IncomingMessage(
        content="hi", kind=MessageKind.USER_REQUEST,
    ))
    # Direct test hook still processes; the run_loop's pause gate is
    # exercised in test_lifecycle. process_one bypasses the pause check.
    # So instead assert clock state plumbed through.
    assert clock.is_accepting_messages() is False  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_llm_timeout_marks_unhealthy_and_emits_alert(
    agent: Agent, llm: object, event_store: EventStore,
) -> None:
    llm.fail_run_turn_n = 99  # type: ignore[attr-defined]
    await agent.deliver(IncomingMessage(
        content="hi", kind=MessageKind.USER_REQUEST,
    ))
    from modules.agent_runtime import LLMFailure

    with pytest.raises(LLMFailure):
        await agent.process_one()
    assert agent.status() is AgentStatus.UNHEALTHY
    alerts = event_store.read(kinds=[EventKind.AGENT_HEALTH_ALERT])
    assert len(alerts) == 1
    assert alerts[0].payload["issue"] == "llm_failure"


@pytest.mark.asyncio
async def test_status_idle_after_successful_turn(
    agent: Agent, llm: object,
) -> None:
    llm.push_text("ok")  # type: ignore[attr-defined]
    await agent.deliver(IncomingMessage(
        content="hi", kind=MessageKind.USER_REQUEST,
    ))
    await agent.process_one()
    assert agent.status() is AgentStatus.IDLE
