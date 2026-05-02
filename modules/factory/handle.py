"""The :class:`CompanyHandle` returned by :class:`CompanyFactory`.

A handle is the root of a fully-wired company. Tests, Board API, and the
factory itself use it to reach the underlying services. ``shutdown`` is a
hard stop; the soft, archive-aware path lives on
:meth:`CompanyFactory.close_company`.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from modules.agent_runtime import Agent
from modules.approvals import Approvals
from modules.clock import Clock
from modules.connector import Connector
from modules.cost import Budget, Pricing, Subscriptions
from modules.efficiency import EfficiencyService
from modules.event_store import EventStore
from modules.identity import Org
from modules.memory import Memory
from modules.orchestrator import Orchestrator
from modules.performance import Performance
from modules.security_agent import SecurityPolicy
from modules.storage import CompanyDB, Quota, Workspace
from modules.tools import Tools


@dataclass
class CompanyHandle:
    """Bundle of every service the company needs at runtime."""

    company_id: str
    db: CompanyDB
    workspace: Workspace
    quota: Quota
    events: EventStore
    clock: Clock
    identity: Org
    pricing: Pricing
    budget: Budget
    subscriptions: Subscriptions
    approvals: Approvals
    memory: Memory
    tools: Tools
    connector: Connector
    orchestrator: Orchestrator
    agents: dict[str, Agent]
    security_policy: SecurityPolicy
    bootstrap_agent_ids: dict[str, str]  # role-name → agent_id
    company_mission: str = ""
    default_agent_quota_mb: int = 64
    efficiency: EfficiencyService | None = None
    performance: Performance | None = None

    async def shutdown(self) -> None:
        """Stop everything — used by tests and the factory close path.

        Best-effort: each step is wrapped in ``contextlib.suppress`` so a
        partial bring-up can still be torn down cleanly.
        """
        for agent in list(self.agents.values()):
            with contextlib.suppress(Exception):
                await agent.stop(drain=False)
        if self.efficiency is not None:
            with contextlib.suppress(Exception):
                await self.efficiency.stop()
        with contextlib.suppress(Exception):
            await self.orchestrator.stop()
        with contextlib.suppress(Exception):
            await self.clock.stop()
        # Make sure nothing was left in flight.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(0)
        with contextlib.suppress(Exception):
            self.db.close()


__all__ = ["CompanyHandle"]
