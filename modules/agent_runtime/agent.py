"""Per-agent lifecycle: a persistent asyncio task that drains an inbox queue.

The :class:`Agent` is fully self-contained: it owns its inbox, a status
field, and a :class:`HealthTracker`. It pulls dependencies through a
constructor-injected :class:`AgentDeps` bundle so tests can wire fakes for
every collaborator.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules.clock import Clock
from modules.cost import Budget
from modules.event_store import EventKind, EventStore
from modules.identity import Agent as IdentityAgent
from modules.identity import Org
from modules.memory import Memory
from modules.tools import Tools

from .exceptions import LLMFailure, TurnTimeout
from .health import HealthTracker
from .models import AgentStatus, HealthSignals, IncomingMessage
from .protocols import LLMClient, PersonaLoader
from .turn import TurnOutcome, run_turn

_log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class AgentDeps:
    identity: Org
    memory: Memory
    tools: Tools
    events: EventStore
    cost: Budget
    clock: Clock
    llm: LLMClient
    persona_loader: PersonaLoader
    company_id: str = "company"
    company_mission: str = "(unset)"


@dataclass
class AgentContext:
    """Bundle the turn loop reads. Built once per :class:`Agent` instance."""

    agent: IdentityAgent
    company_id: str
    company_mission: str
    identity: Org
    memory: Memory
    tools: Tools
    events: EventStore
    llm: LLMClient
    persona_loader: PersonaLoader
    health: HealthTracker
    monotonic: Callable[[], float]


class Agent:
    """One agent's lifecycle: queue + run loop + status."""

    def __init__(self, agent_id: str, deps: AgentDeps) -> None:
        self._agent_id = agent_id
        self._deps = deps
        self._queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        self._status = AgentStatus.STOPPED
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._inflight: asyncio.Event = asyncio.Event()
        self._inflight.set()  # not currently working
        self._record = deps.identity.get(agent_id)
        self._health = HealthTracker(now=_utcnow())
        self._ctx = AgentContext(
            agent=self._record,
            company_id=deps.company_id,
            company_mission=deps.company_mission,
            identity=deps.identity,
            memory=deps.memory,
            tools=deps.tools,
            events=deps.events,
            llm=deps.llm,
            persona_loader=deps.persona_loader,
            health=self._health,
            monotonic=time.monotonic,
        )

    # --------------------------------------------------------------- props

    @property
    def id(self) -> str:
        return self._agent_id

    def status(self) -> AgentStatus:
        return self._status

    def health_signals(self) -> HealthSignals:
        return self._health.snapshot()

    @property
    def inbox_size(self) -> int:
        return self._queue.qsize()

    # -------------------------------------------------------------- public

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._status = AgentStatus.IDLE
        # Validate persona resolves at startup so failures surface early.
        self._deps.persona_loader.load(
            self._record.role,
            self._record.persona_ref or "default",
            {
                "agent_id": self._record.id,
                "role": self._record.role.value,
                "role_title": self._record.role_title or self._record.role.value,
                "role_description": self._record.role_description or "(none)",
                "first_name": self._record.first_name,
                "last_name": self._record.last_name,
                "agent_name": (
                    f"{self._record.first_name} {self._record.last_name}".strip()
                    or self._record.id
                ),
                "company_id": self._deps.company_id,
                "manager_name": self._record.reports_to or "(none)",
                "direct_reports": "(none)",
                "company_mission": self._deps.company_mission,
                "available_tools": "",
                "first_name": self._record.first_name,
                "last_name": self._record.last_name,
                "agent_name": f"{self._record.first_name} {self._record.last_name}".strip() or self._record.id,
                "role_title": self._record.role_title or self._record.role.value,
                "role_description": self._record.role_description or "",
            },
        )
        self._task = asyncio.create_task(self._run_loop(), name=f"agent:{self._agent_id}")

    async def stop(self, drain: bool = True) -> None:
        self._stop_event.set()
        if drain and not self._inflight.is_set():
            try:
                await asyncio.wait_for(self._inflight.wait(), timeout=5.0)
            except TimeoutError:
                _log.warning("stop drain timed out", extra={"agent_id": self._agent_id})
        if self._task is not None:
            await self._queue.put(_SENTINEL)
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except TimeoutError:
                self._task.cancel()
        self._task = None
        self._status = AgentStatus.STOPPED

    async def deliver(self, message: IncomingMessage) -> None:
        await self._queue.put(message)

    async def process_one(self) -> TurnOutcome | None:
        """Test hook: drain a single message synchronously."""
        try:
            msg = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        if msg is _SENTINEL:
            return None
        return await self._handle_message(msg)

    # ------------------------------------------------------------- internals

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            msg = await self._queue.get()
            if msg is _SENTINEL:
                break
            if not self._deps.clock.is_accepting_messages():
                # Re-queue and yield until accepting again.
                await self._queue.put(msg)
                await asyncio.sleep(0.05)
                continue
            await self._handle_message(msg)

    async def _handle_message(self, msg: IncomingMessage) -> TurnOutcome:
        self._inflight.clear()
        self._status = AgentStatus.WORKING
        try:
            self._record = self._deps.identity.get(self._agent_id)
            self._ctx.agent = self._record
            outcome = await run_turn(self._ctx, msg, now=_utcnow())
        except (LLMFailure, TurnTimeout) as exc:
            self._health.record_error()
            self._status = AgentStatus.UNHEALTHY
            _log.warning(
                "agent turn failed agent=%s issue=%s detail=%s",
                self._agent_id, exc.code, exc, exc_info=True,
            )
            self._deps.events.append(
                EventKind.AGENT_HEALTH_ALERT,
                {
                    "agent_id": self._agent_id,
                    "issue": exc.code,
                    "severity": "warn",
                    "detail": str(exc)[:500],
                },
                actor=self._agent_id,
            )
            raise
        finally:
            self._inflight.set()

        if outcome.task_done:
            await self._summarize_and_remember(msg, outcome)

        self._status = AgentStatus.IDLE
        return outcome

    async def _summarize_and_remember(
        self, msg: IncomingMessage, outcome: TurnOutcome,
    ) -> None:
        summary = outcome.summary
        if not summary:
            history = self._deps.memory.working_window(self._agent_id, n=20)
            try:
                summary = await self._deps.llm.completion(
                    "Summarise what was accomplished, decisions made, and key facts learned.",
                    context=history,
                )
            except Exception as exc:
                _log.warning("summary completion failed", extra={"agent_id": self._agent_id, "error": str(exc)})
                summary = outcome.text[:500] or "(no summary)"
        self._deps.memory.remember_episode(
            self._agent_id, summary,
            metadata={"correlation_id": msg.correlation_id or ""},
        )


_SENTINEL: Any = object()


__all__ = ["Agent", "AgentContext", "AgentDeps"]
