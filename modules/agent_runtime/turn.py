"""Single-turn driver.

Public entry point: :func:`run_turn`. Given an :class:`IncomingMessage` and
the agent's dependency bundle, it executes one full LLM round-trip:

1. Append the message to working memory.
2. Recall related episodes/facts.
3. Build the system prompt (persona + recall block).
4. Stream LLM events. For each tool-use event, dispatch to the tool layer
   and submit the result back to the LLM client.
5. Persist text chunks to working memory and return the concatenated
   assistant response.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from modules.event_store import EventKind
from modules.memory import RecallHit, WorkingItem

from .exceptions import LLMFailure, TurnTimeout
from .models import IncomingMessage

if TYPE_CHECKING:
    from .agent import AgentContext

_log = logging.getLogger(__name__)

_TASK_DONE_OPEN = "<task_done"
_TASK_DONE_CLOSE = "/>"
_LLM_TIMEOUT_SECONDS = 90.0
_LLM_RETRY_ATTEMPTS = 2


@dataclass(frozen=True)
class TurnOutcome:
    text: str
    tool_invocations: int
    task_done: bool
    summary: str | None
    duration_seconds: float
    truncated_reason: str | None = None
    """Set to the LLM's ``stop_reason`` when the turn was cut off (e.g.
    ``"tool_loop_cap"``). ``None`` when the turn ran to a clean stop."""


def _format_recall(hits: list[RecallHit]) -> str:
    if not hits:
        return "No relevant memories."
    lines = ["Relevant memories:"]
    for h in hits:
        lines.append(f"- ({h.kind.value}, score={h.score:.2f}) {h.content}")
    return "\n".join(lines)


def _persona_ctx(ctx: AgentContext) -> Mapping[str, Any]:
    direct_reports = ctx.identity.direct_reports(ctx.agent.id)
    manager_name = ctx.agent.reports_to or "(none)"
    tool_names = [t.name for t in ctx.tools.for_role(ctx.agent.role)]
    role_title = ctx.agent.role_title or ctx.agent.role.value
    return {
        "agent_id": ctx.agent.id,
        "role": ctx.agent.role.value,
        "role_title": role_title,
        "role_description": ctx.agent.role_description or "(none)",
        "first_name": ctx.agent.first_name,
        "last_name": ctx.agent.last_name,
        "agent_name": (
            f"{ctx.agent.first_name} {ctx.agent.last_name}".strip()
            or ctx.agent.id
        ),
        "company_id": ctx.company_id,
        "manager_name": manager_name,
        "direct_reports": ", ".join(direct_reports) or "(none)",
        "company_mission": ctx.company_mission,
        "available_tools": ", ".join(tool_names),
        "first_name": ctx.agent.first_name,
        "last_name": ctx.agent.last_name,
        "agent_name": f"{ctx.agent.first_name} {ctx.agent.last_name}".strip() or ctx.agent.id,
        "role_title": ctx.agent.role_title or ctx.agent.role.value,
        "role_description": ctx.agent.role_description or "",
    }


def _detect_task_done(text: str) -> tuple[bool, str | None]:
    """Return ``(done, summary)`` if a ``<task_done summary="..." />`` marker
    is present anywhere in the text. Summary is optional."""
    idx = text.find(_TASK_DONE_OPEN)
    if idx < 0:
        return False, None
    end = text.find(_TASK_DONE_CLOSE, idx)
    if end < 0:
        return False, None
    body = text[idx + len(_TASK_DONE_OPEN) : end]
    summary: str | None = None
    if "summary=" in body:
        try:
            after = body.split("summary=", 1)[1].strip()
            quote = after[0]
            if quote in ("'", '"'):
                close = after.find(quote, 1)
                if close > 0:
                    summary = after[1:close]
        except (IndexError, ValueError):
            summary = None
    return True, summary


def _tool_correlation_id(
    message_correlation_id: str | None,
    tool_use_id: str,
    tool_invocation_index: int,
) -> str:
    """Stable idempotency key for one LLM tool-use event.

    A turn can contain multiple tool calls. The incoming message correlation
    identifies the turn, not a specific tool call; using it directly makes
    the Tool Layer treat every later tool call as a duplicate of the first.
    """
    base = message_correlation_id or "turn"
    suffix = tool_use_id or str(tool_invocation_index)
    return f"{base}:{suffix}"


async def _run_llm_with_retry(
    ctx: AgentContext, system: str,
    history: list[WorkingItem], tool_schemas: list[dict[str, Any]],
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, _LLM_RETRY_ATTEMPTS + 2):
        try:
            return ctx.llm.run_turn(
                system=system, history=history, tool_schemas=tool_schemas,
                cache_keys=[
                    "persona",
                    ctx.agent.role.value,
                    ctx.agent.role_title or "",
                ],
            )
        except TimeoutError as exc:
            last_exc = exc
        except LLMFailure as exc:
            last_exc = exc
        except Exception as exc:
            last_exc = exc
        if attempt > _LLM_RETRY_ATTEMPTS:
            break
        await asyncio.sleep(0.01 * (2 ** (attempt - 1)))
    _log.warning(
        "llm.run_turn failed for agent=%s: %s",
        ctx.agent.id, last_exc, exc_info=last_exc,
    )
    raise LLMFailure(str(last_exc) if last_exc else "llm_unavailable")


async def run_turn(
    ctx: AgentContext, msg: IncomingMessage, *, now: datetime,
) -> TurnOutcome:
    started = ctx.monotonic()

    ctx.memory.append_working(
        ctx.agent.id,
        WorkingItem(
            role="user",
            content=msg.content,
            ts_company=now,
            metadata={
                "kind": msg.kind.value,
                "from_agent": msg.from_agent or "",
                "correlation_id": msg.correlation_id or "",
            },
        ),
    )

    ctx.events.append(
        EventKind.MESSAGE_DELIVERED,
        {
            "message_id": msg.correlation_id or f"m-{int(now.timestamp() * 1000)}",
            "to_agent": ctx.agent.id,
        },
        actor=msg.from_agent,
        correlation=msg.correlation_id,
    )

    # Recall context.
    hits: list[RecallHit] = ctx.memory.recall(
        ctx.agent.id, msg.content[:500], k=5,
    )
    recall_block = _format_recall(hits)

    persona = ctx.persona_loader.load(
        ctx.agent.role, ctx.agent.persona_ref or "default",
        _persona_ctx(ctx),
    )
    system = f"{persona}\n\n{recall_block}"

    history = ctx.memory.working_window(ctx.agent.id, n=20)
    tool_schemas = ctx.tools.claude_sdk_schema(ctx.agent.role)

    # ---- LLM streaming loop
    try:
        stream = await asyncio.wait_for(
            _run_llm_with_retry(ctx, system, history, tool_schemas),
            timeout=_LLM_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        ctx.health.record_error()
        raise TurnTimeout("llm timeout") from exc

    text_chunks: list[str] = []
    tool_invocations = 0
    replied_to_sender = False
    stop_reason: str | None = None
    try:
        stream_iter = stream.__aiter__()
        while True:
            try:
                ev = await asyncio.wait_for(
                    stream_iter.__anext__(), timeout=_LLM_TIMEOUT_SECONDS,
                )
            except StopAsyncIteration:
                break
            if ev.type == "stop":
                stop_reason = ev.stop_reason or None
                continue
            if ev.type == "tool_use":
                tool_invocations += 1
                try:
                    result = await ctx.tools.invoke(
                        agent_id=ctx.agent.id,
                        tool_name=ev.tool_name,
                        args=dict(ev.tool_input),
                        correlation_id=_tool_correlation_id(
                            msg.correlation_id,
                            ev.tool_use_id,
                            tool_invocations,
                        ),
                    )
                except Exception as exc:
                    ctx.health.record_error()
                    await ctx.llm.submit_tool_result(
                        tool_use_id=ev.tool_use_id, ok=False,
                        output={"error": str(exc)},
                    )
                    continue
                if (
                    result.ok
                    and ev.tool_name == "send_message"
                    and msg.from_agent
                    and dict(ev.tool_input).get("recipient_id") == msg.from_agent
                ):
                    replied_to_sender = True
                payload: dict[str, Any] = {
                    "ok": result.ok,
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                }
                output_dump: dict[str, Any] | None = None
                if result.output is not None:
                    output_dump = result.output.model_dump()
                    payload["output"] = output_dump

                # Detect "soft rejections": tool call returned ok=True but
                # the downstream service rejected it (e.g. send_message
                # whose message_id ends with ":rejected:rejected_loop").
                # The agent has been retrying the same payload because
                # ok=True looked like success. Surface this clearly so the
                # LLM sees a failure on the next turn instead of looping.
                rejection_reason: str | None = None
                if not result.ok:
                    rejection_reason = (
                        result.error_message
                        or result.error_code
                        or "tool_failed"
                    )
                elif (
                    ev.tool_name == "send_message"
                    and output_dump is not None
                ):
                    msg_id = str(output_dump.get("message_id", ""))
                    if ":rejected:" in msg_id:
                        rejection_reason = msg_id.split(":rejected:", 1)[1]
                if rejection_reason:
                    payload["rejected"] = True
                    payload["rejection_reason"] = rejection_reason

                effective_ok = result.ok and rejection_reason is None
                await ctx.llm.submit_tool_result(
                    tool_use_id=ev.tool_use_id,
                    ok=effective_ok,
                    output=payload,
                )
                ctx.memory.append_working(
                    ctx.agent.id,
                    WorkingItem(
                        role="tool",
                        content=f"{ev.tool_name}: ok={effective_ok}",
                        ts_company=now,
                        metadata={"tool": ev.tool_name},
                    ),
                )
                if rejection_reason:
                    ctx.memory.append_working(
                        ctx.agent.id,
                        WorkingItem(
                            role="system",
                            content=(
                                f"Your last `{ev.tool_name}` call was "
                                f"rejected: {rejection_reason}. Do NOT "
                                "retry the same call — change something "
                                "concrete (different recipient, different "
                                "reports_to, different content) or "
                                "escalate to your manager."
                            ),
                            ts_company=now,
                            metadata={
                                "tool": ev.tool_name,
                                "rejection": rejection_reason,
                            },
                        ),
                    )
            elif ev.type == "text":
                text_chunks.append(ev.text)
    except (LLMFailure, TurnTimeout):
        ctx.health.record_error()
        raise
    except TimeoutError as exc:
        _log.warning(
            "llm stream timed out for agent=%s", ctx.agent.id, exc_info=True,
        )
        ctx.health.record_error()
        raise TurnTimeout("llm stream timeout") from exc
    except Exception as exc:
        _log.warning(
            "llm stream failed for agent=%s: %s",
            ctx.agent.id, exc, exc_info=True,
        )
        ctx.health.record_error()
        raise LLMFailure(str(exc) or "llm_stream_error") from exc

    full_text = "".join(text_chunks)
    if full_text:
        ctx.memory.append_working(
            ctx.agent.id,
            WorkingItem(role="agent", content=full_text, ts_company=now),
        )
        ctx.health.record_response(full_text)

    truncated = stop_reason == "tool_loop_cap"
    if truncated:
        ctx.events.append(
            EventKind.AGENT_TURN_TRUNCATED,
            {
                "agent_id": ctx.agent.id,
                "reason": stop_reason,
                "tool_invocations": tool_invocations,
            },
            actor=ctx.agent.id,
            correlation=msg.correlation_id,
        )
        # Drop a system note into working memory so the agent's next turn
        # starts with the awareness that the previous turn was cut short
        # and should resume rather than restart from scratch.
        ctx.memory.append_working(
            ctx.agent.id,
            WorkingItem(
                role="system",
                content=(
                    "Your previous turn was cut off at the tool-call cap "
                    f"after {tool_invocations} tool calls. Continue from "
                    "where you left off — do not restart the plan."
                ),
                ts_company=now,
            ),
        )

    # Safety net: if another agent (not the system) sent us a message and we
    # finished the turn without replying to them, auto-send a short reply so
    # the conversation never silently dies. We pick the LLM's own free-text
    # output when available, otherwise fall back to a neutral acknowledgement.
    if (
        msg.from_agent
        and msg.from_agent != "system"
        and not replied_to_sender
    ):
        body = (full_text.strip() or "Understood.")[:1500]
        try:
            await ctx.tools.invoke(
                agent_id=ctx.agent.id,
                tool_name="send_message",
                args={"recipient_id": msg.from_agent, "body": body},
                correlation_id=f"{msg.correlation_id or 'turn'}:auto_ack",
            )
        except Exception:
            _log.warning(
                "auto_ack failed for agent=%s", ctx.agent.id, exc_info=True,
            )

    done, summary = _detect_task_done(full_text)

    duration = ctx.monotonic() - started
    ctx.health.record_turn(duration_seconds=duration, ts=now)
    ctx.health.reset_error_streak()

    ctx.events.append(
        EventKind.AGENT_TURN_COMPLETED,
        {
            "agent_id": ctx.agent.id,
            "task_done": done,
            "duration_seconds": duration,
            "tool_invocations": tool_invocations,
        },
        actor=ctx.agent.id,
        correlation=msg.correlation_id,
    )

    ctx.events.append(
        EventKind.AGENT_HEARTBEAT,
        {"agent_id": ctx.agent.id, "state": "idle"},
        actor=ctx.agent.id,
    )

    return TurnOutcome(
        text=full_text,
        tool_invocations=tool_invocations,
        task_done=done,
        summary=summary,
        duration_seconds=duration,
        truncated_reason=stop_reason if truncated else None,
    )


__all__ = ["TurnOutcome", "run_turn"]
