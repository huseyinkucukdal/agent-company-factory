"""The Orchestrator: per-company message router and health watchdog.

Responsibilities (see ``PLAN.md`` for the full spec):

* Validate ``send`` requests against identity, pause-state, rate limit,
  loop detector and content-size cap.
* Persist a :class:`MessageEnvelope` into the per-agent queue.
* Run one consumer task per registered agent that pulls envelopes and
  forwards them to :meth:`AgentHandle.deliver`.
* Periodically score agent health; emit ``agent.health_alert`` and notify
  the manager when an agent slips below the cutoff.

The Redis-backed message queue is abstracted behind :class:`MessageQueue`,
so this class is fully testable with the in-memory implementation. The
"system_send" path (board / orchestrator-originated traffic) bypasses
the rate limiter and loop detector but still respects pause and target
validity.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from modules.agent_runtime import AgentStatus, IncomingMessage, MessageKind
from modules.clock import Clock
from modules.event_store import EventKind, EventStore
from modules.identity import Org, Status

from .exceptions import RedisUnavailable
from .health_monitor import UNHEALTHY_CUTOFF, compute_score
from .loop_detector import LoopDetector, LoopDetectorConfig
from .models import AgentHealth, IdleSweepConfig, MessageEnvelope, SendResult
from .protocols import AgentHandle, MessageQueue
from .rate_limit import RateLimitConfig, RateLimiter
from .streams import InMemoryMessageQueue

_log = logging.getLogger(__name__)

_MAX_CONTENT_BYTES = 256 * 1024
_DEADLOCK_BLOCKED_AFTER = timedelta(minutes=30)
_COMPANY_STUCK_AFTER = timedelta(hours=1)
_LOOP_STRIKES_BEFORE_SUSPEND = 3


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Orchestrator:
    """Per-company message-routing core."""

    def __init__(
        self,
        company_id: str,
        *,
        identity: Org,
        events: EventStore,
        clock: Clock,
        queue: MessageQueue | None = None,
        rate_limit: RateLimitConfig | None = None,
        loop_config: LoopDetectorConfig | None = None,
        idle_sweep: IdleSweepConfig | None = None,
        now: Callable[[], datetime] = _utcnow,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._company_id = company_id
        self._identity = identity
        self._events = events
        self._clock = clock
        self._queue: MessageQueue = queue or InMemoryMessageQueue()
        self._rate = RateLimiter(monotonic=monotonic, config=rate_limit)
        self._loops = LoopDetector(loop_config)
        self._idle_cfg = idle_sweep or IdleSweepConfig()
        self._now = now
        self._agents: dict[str, AgentHandle] = {}
        self._consumers: dict[str, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()
        self._loop_strikes: dict[str, int] = {}
        self._suspended: set[str] = set()
        self._last_message_at: datetime = self._call_now()
        self._blocked_since: dict[str, datetime] = {}
        self._last_health_alert: dict[str, datetime] = {}
        self._last_activity_at: dict[str, datetime] = {}
        self._last_nudge_at: dict[str, datetime] = {}
        self._watchdog_task: asyncio.Task[None] | None = None

    # --------------------------------------------------------------- helpers

    def _call_now(self) -> datetime:
        return self._now()

    # --------------------------------------------------------------- registry

    def register(self, agent: AgentHandle) -> None:
        """Add an agent to the routing table."""
        self._agents[agent.id] = agent
        self._last_activity_at[agent.id] = self._call_now()

    def unregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        task = self._consumers.pop(agent_id, None)
        if task is not None and not task.done():
            task.cancel()

    # --------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Spawn one consumer task per registered agent."""
        self._stop_event.clear()
        for aid, agent in self._agents.items():
            if aid in self._consumers and not self._consumers[aid].done():
                continue
            self._consumers[aid] = asyncio.create_task(
                self._consume_loop(agent), name=f"orch:{aid}",
            )
        if (
            self._idle_cfg.enabled
            and (self._watchdog_task is None or self._watchdog_task.done())
        ):
            self._watchdog_task = asyncio.create_task(
                self._watchdog_loop(),
                name=f"orch-watchdog:{self._company_id}",
            )

    async def stop(self) -> None:
        self._stop_event.set()
        tasks = list(self._consumers.values())
        if self._watchdog_task is not None:
            tasks.append(self._watchdog_task)
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._consumers.clear()
        self._watchdog_task = None

    # ------------------------------------------------------------------ send

    async def send(
        self, *, from_agent: str, to_agent: str, content: str,
        correlation_id: str | None = None,
        kind: MessageKind = MessageKind.AGENT_MESSAGE,
    ) -> SendResult:
        return await self._send(
            from_agent=from_agent, to_agent=to_agent, content=content,
            correlation_id=correlation_id, kind=kind, system=False,
        )

    async def system_send(
        self, to_agent: str, content: str,
        kind: MessageKind = MessageKind.NOTIFY,
        *, correlation_id: str | None = None,
    ) -> SendResult:
        return await self._send(
            from_agent=None, to_agent=to_agent, content=content,
            correlation_id=correlation_id, kind=kind, system=True,
        )

    async def _send(
        self, *, from_agent: str | None, to_agent: str, content: str,
        correlation_id: str | None, kind: MessageKind, system: bool,
    ) -> SendResult:
        # 1. Size cap (cheap reject before any state lookup).
        if len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            return SendResult.REJECTED_TOO_LARGE

        # 2. Validate sender (only for agent-originated traffic).
        if from_agent is not None and not self._is_active(from_agent):
            return SendResult.REJECTED_INVALID_TARGET

        # 3. Validate recipient.
        if not self._is_active(to_agent) or to_agent in self._suspended:
            if from_agent is not None:
                await self._notify_sender_invalid(from_agent, to_agent)
            return SendResult.REJECTED_INVALID_TARGET
        if to_agent not in self._agents:
            return SendResult.REJECTED_INVALID_TARGET

        # 4. Pause gate.
        if not self._clock.is_accepting_messages():
            return SendResult.REJECTED_PAUSED

        # 5. Rate limit (system traffic is exempt).
        if not system and not self._rate.try_consume(from_agent or "?"):
            await self._notify_slow_down(from_agent or "?")
            return SendResult.REJECTED_RATE_LIMIT

        # 6. Loop detection (skip system).
        if not system:
            looped, kind_of_loop = self._loops.check(
                from_agent=from_agent, to_agent=to_agent, content=content,
            )
            if looped:
                await self._handle_loop(to_agent, kind_of_loop or "loop")
                return SendResult.REJECTED_LOOP

        # 7. Idempotency on correlation_id.
        if correlation_id and self._queue.has_correlation(to_agent, correlation_id):
            return SendResult.REJECTED_DUPLICATE

        # 8. Persist envelope.
        env = MessageEnvelope.new(
            from_agent=from_agent, to_agent=to_agent, content=content,
            kind=kind, correlation_id=correlation_id,
            enqueued_at=self._call_now(),
        )
        try:
            self._queue.push(env)
        except RedisUnavailable:
            self._events.append(
                EventKind.AGENT_HEALTH_ALERT,
                {
                    "agent_id": to_agent, "issue": "queue_unavailable",
                    "severity": "warn",
                },
                actor=from_agent,
            )
            raise

        self._events.append(
            EventKind.MESSAGE_SENT,
            {
                "from_agent": from_agent or "system",
                "to_agent": to_agent, "content": content,
                "thread_id": correlation_id,
            },
            actor=from_agent,
            correlation=correlation_id,
        )
        now = self._call_now()
        self._last_message_at = now
        if from_agent:
            self._last_activity_at[from_agent] = now
        return SendResult.QUEUED

    # ----------------------------------------------------- consumer plumbing

    async def _consume_loop(self, agent: AgentHandle) -> None:
        try:
            async for env in self._queue.aiter(agent.id):
                if self._stop_event.is_set():
                    break
                if not self._clock.is_accepting_messages():
                    # Re-queue and back off; the pause gate keeps rejecting
                    # fresh sends so the queue cannot grow unboundedly.
                    self._queue.push(env)
                    await asyncio.sleep(0.05)
                    continue
                await self._dispatch(agent, env)
        except asyncio.CancelledError:
            return

    async def _dispatch(self, agent: AgentHandle, env: MessageEnvelope) -> None:
        msg = IncomingMessage(
            content=env.content, kind=env.kind,
            from_agent=env.from_agent,
            correlation_id=env.correlation_id or env.msg_id,
        )
        try:
            await agent.deliver(msg)
            self._last_activity_at[agent.id] = self._call_now()
        except Exception as exc:
            _log.exception("delivery failed", extra={"agent_id": agent.id})
            attempts = env.attempts + 1
            if attempts >= 3:
                self._queue.to_dlq(env, str(exc), now=self._call_now())
            else:
                # Re-enqueue with bumped attempt counter.
                self._queue.push(
                    MessageEnvelope(
                        msg_id=env.msg_id, from_agent=env.from_agent,
                        to_agent=env.to_agent, content=env.content,
                        kind=env.kind, correlation_id=env.correlation_id,
                        enqueued_at=env.enqueued_at, attempts=attempts,
                    ),
                )

    async def drain(self, *, max_iters: int = 100) -> int:
        """Test hook: synchronously dispatch every queued envelope.

        Iterates round-robin until all per-agent queues are empty or the
        iteration cap is hit (defensive guard against re-queue loops).
        """
        delivered = 0
        for _ in range(max_iters):
            progressed = False
            for aid, agent in list(self._agents.items()):
                env = self._queue.pop(aid)
                if env is None:
                    continue
                if not self._clock.is_accepting_messages():
                    self._queue.push(env)
                    continue
                await self._dispatch(agent, env)
                delivered += 1
                progressed = True
            if not progressed:
                break
        return delivered

    # ---------------------------------------------------------------- queries

    def is_company_idle(self) -> bool:
        if not self._agents:
            return True
        for aid, agent in self._agents.items():
            if self._queue.pending_count(aid) > 0:
                return False
            if agent.status() not in (AgentStatus.IDLE, AgentStatus.STOPPED):
                return False
        return True

    def health(self, agent_id: str) -> AgentHealth:
        agent = self._require_agent(agent_id)
        signals = agent.health_signals()
        score, notes = compute_score(signals, now=self._call_now())
        return AgentHealth(
            agent_id=agent_id,
            score=score,
            status=agent.status().value,
            consecutive_errors=signals.consecutive_errors,
            parse_failures=signals.parse_failures,
            repeat_response_count=signals.repeat_response_count,
            avg_turn_seconds=signals.avg_turn_seconds,
            last_heartbeat=signals.last_heartbeat,
            notes=notes,
        )

    def dlq(self) -> list[Any]:
        return list(self._queue.dlq())

    def is_suspended(self, agent_id: str) -> bool:
        return agent_id in self._suspended

    def suspend(self, agent_id: str, *, reason: str = "security") -> None:
        """Public suspension hook used by Security Agent et al.

        Adds the agent to the suspended set, emits a health alert and
        notifies the manager. Idempotent.
        """
        if agent_id in self._suspended:
            return
        self._suspended.add(agent_id)
        self._events.append(
            EventKind.AGENT_HEALTH_ALERT,
            {
                "agent_id": agent_id, "severity": "high",
                "issue": f"suspended:{reason}",
            },
            actor=agent_id,
        )
        self._notify_manager_suspended(agent_id)

    def resume(self, agent_id: str) -> None:
        """Lift a previous :meth:`suspend`. Idempotent."""
        self._suspended.discard(agent_id)

    # -------------------------------------------------- watchdog (callable from outside)

    def health_sweep(self) -> list[AgentHealth]:
        """Score every agent; emit alerts and notify managers when needed."""
        out: list[AgentHealth] = []
        now = self._call_now()
        for aid in list(self._agents):
            h = self.health(aid)
            out.append(h)
            if h.score < UNHEALTHY_CUTOFF:
                last = self._last_health_alert.get(aid)
                if last is None or (now - last) > timedelta(minutes=1):
                    self._last_health_alert[aid] = now
                    self._events.append(
                        EventKind.AGENT_HEALTH_ALERT,
                        {
                            "agent_id": aid, "severity": "warn",
                            "issue": ",".join(h.notes) or "unhealthy",
                        },
                        actor=aid,
                    )
                    self._notify_manager_unhealthy(aid)

            agent = self._agents[aid]
            if agent.status() is AgentStatus.BLOCKED:
                self._blocked_since.setdefault(aid, now)
            else:
                self._blocked_since.pop(aid, None)
        self._check_deadlock(now)
        self._check_company_stuck(now)
        return out

    # --------------------------------------------------------------- internals

    def _require_agent(self, agent_id: str) -> AgentHandle:
        agent = self._agents.get(agent_id)
        if agent is None:
            from .exceptions import InvalidTarget

            raise InvalidTarget(agent_id)
        return agent

    def _is_active(self, agent_id: str) -> bool:
        try:
            return self._identity.get(agent_id).status is Status.ACTIVE
        except KeyError:
            return False

    async def _handle_loop(self, agent_id: str, kind: str) -> None:
        self._loop_strikes[agent_id] = self._loop_strikes.get(agent_id, 0) + 1
        self._events.append(
            EventKind.AGENT_HEALTH_ALERT,
            {
                "agent_id": agent_id, "severity": "warn",
                "issue": f"loop_{kind}",
            },
            actor=agent_id,
        )
        if self._loop_strikes[agent_id] >= _LOOP_STRIKES_BEFORE_SUSPEND:
            self._suspended.add(agent_id)
            self._notify_manager_suspended(agent_id)

    async def _notify_sender_invalid(self, sender: str, fired: str) -> None:
        await self.system_send(
            sender,
            f"Recipient {fired} is not deliverable.",
            kind=MessageKind.NOTIFY,
        )

    async def _notify_slow_down(self, agent_id: str) -> None:
        # Rate-limit notifications themselves do not bypass safety gates,
        # but the recipient is the same agent that just over-sent — fire-
        # and-forget is fine.
        with contextlib.suppress(Exception):
            await self.system_send(
                agent_id, "rate limit reached, slow down",
                kind=MessageKind.NOTIFY,
            )

    def _notify_manager_unhealthy(self, agent_id: str) -> None:
        try:
            mgr = self._identity.manager_of(agent_id)
        except KeyError:
            mgr = None
        if not mgr:
            return
        with contextlib.suppress(RuntimeError):
            asyncio.get_event_loop().create_task(
                self.system_send(
                    mgr,
                    f"Agent {agent_id} is unhealthy. Consider intervention.",
                    kind=MessageKind.NOTIFY,
                ),
            )

    def _notify_manager_suspended(self, agent_id: str) -> None:
        try:
            mgr = self._identity.manager_of(agent_id)
        except KeyError:
            mgr = None
        if not mgr:
            return
        self._events.append(
            EventKind.AGENT_HEALTH_ALERT,
            {
                "agent_id": agent_id, "severity": "warn",
                "issue": "suspended_loop_strikes",
            },
            actor=agent_id,
        )

    def _check_deadlock(self, now: datetime) -> None:
        for aid, since in self._blocked_since.items():
            if (now - since) > _DEADLOCK_BLOCKED_AFTER:
                self._events.append(
                    EventKind.AGENT_HEALTH_ALERT,
                    {
                        "agent_id": aid, "severity": "warn",
                        "issue": "deadlock_warning",
                    },
                    actor=aid,
                )

    def _check_company_stuck(self, now: datetime) -> None:
        if (now - self._last_message_at) > _COMPANY_STUCK_AFTER:
            for aid, agent in self._agents.items():
                if agent.status() is AgentStatus.IDLE:
                    self._events.append(
                        EventKind.AGENT_HEALTH_ALERT,
                        {
                            "agent_id": aid, "severity": "warn",
                            "issue": "company_stuck",
                        },
                        actor=aid,
                    )
                    return  # one alert is enough

    # ---------------------------------------------------- idle nudge sweep

    async def idle_sweep(self) -> int:
        """Find agents that have gone silent and gently nudge their manager.

        Returns the number of nudges sent. Public so tests and the Board
        UI can trigger a sweep on demand. Idempotent within the
        ``nudge_cooldown_seconds`` window per agent.
        """
        if not self._idle_cfg.enabled:
            return 0
        now = self._call_now()
        threshold = timedelta(seconds=self._idle_cfg.agent_idle_after_seconds)
        cooldown = timedelta(seconds=self._idle_cfg.nudge_cooldown_seconds)
        sent = 0
        for aid, agent in list(self._agents.items()):
            if aid in self._suspended:
                continue
            if agent.status() is not AgentStatus.IDLE:
                continue
            last = self._last_activity_at.get(aid, self._last_message_at)
            if (now - last) < threshold:
                continue
            last_nudge = self._last_nudge_at.get(aid)
            if last_nudge is not None and (now - last_nudge) < cooldown:
                continue
            await self._nudge(aid, idle_for=int((now - last).total_seconds()))
            self._last_nudge_at[aid] = now
            sent += 1
        return sent

    async def _nudge(self, agent_id: str, *, idle_for: int) -> None:
        """Send a system poke at the agent (or its manager) asking what's next."""
        try:
            mgr = self._identity.manager_of(agent_id)
        except KeyError:
            mgr = None

        minutes = max(1, idle_for // 60)
        self._events.append(
            EventKind.AGENT_HEALTH_ALERT,
            {
                "agent_id": agent_id, "severity": "info",
                "issue": "idle_nudge",
            },
            actor=agent_id,
        )

        if mgr and mgr in self._agents:
            content = (
                f"Your subordinate {agent_id} has been idle for approximately {minutes} minutes — "
                "ask what they need, or assign them a concrete task. "
                "If there is no progress, find out why."
            )
            with contextlib.suppress(Exception):
                await self.system_send(
                    mgr, content, kind=MessageKind.NOTIFY,
                )
            return

        # No manager (typically the CEO): self-prompt so the LLM
        # decides what to delegate next.
        content = (
            f"No significant activity in the company for the past {minutes} minutes. "
            "Your team includes HR and Security. What is your next step? "
            "Delegate a concrete task to someone or review your plan."
        )
        with contextlib.suppress(Exception):
            await self.system_send(
                agent_id, content, kind=MessageKind.NOTIFY,
            )

    async def _watchdog_loop(self) -> None:
        """Background task: periodically run health + idle sweeps."""
        interval = max(1.0, self._idle_cfg.interval_seconds)
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=interval,
                    )
                    return  # stop requested
                except TimeoutError:
                    pass
                if not self._clock.is_accepting_messages():
                    continue
                with contextlib.suppress(Exception):
                    self.health_sweep()
                with contextlib.suppress(Exception):
                    await self.idle_sweep()
        except asyncio.CancelledError:
            return

    # ----------------------------------------------------- introspection

    @property
    def queue(self) -> MessageQueue:
        return self._queue

    @property
    def rate(self) -> RateLimiter:
        return self._rate

    def configure_rate_limit(
        self, agent_id: str, *, capacity: int, refill_per_second: float,
    ) -> None:
        self._rate.configure(
            agent_id, capacity=capacity, refill_per_second=refill_per_second,
        )


# Re-export the Mapping import so older type-checkers see it used.
_unused: Mapping[str, Any] = {}


__all__ = ["Orchestrator"]
