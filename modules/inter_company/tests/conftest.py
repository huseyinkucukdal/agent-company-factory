"""Shared fixtures for Module 16 tests.

We bypass the full :class:`CompanyFactory` (which spins up real
orchestrators + agents) in favour of lightweight stubs that mimic the
attributes Module 16 reads:

    * ``factory.get_summary(cid)``        — used to gate against closed companies
    * ``factory.get_handle(cid)``          — entry point to per-company state
    * ``handle.identity.all(status=...)``  — to resolve the inbox agent
    * ``handle.orchestrator.system_send`` — actual delivery
    * ``handle.events.append``             — best-effort event mirror
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from modules.agent_runtime import MessageKind
from modules.board_api.links import (
    LinkRelationship,
    LinkScope,
    LinkService,
    LinkStatus,
)
from modules.factory import CompanyStatus
from modules.identity import Role, Status
from modules.inter_company.service import InterCompanyService
from modules.orchestrator import SendResult
from modules.storage import BoardDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)


# --------------------------------------------------------- agent stubs

@dataclass(frozen=True)
class StubAgent:
    id: str
    role: Role
    status: Status = Status.ACTIVE


# --------------------------------------------------------- runtime stubs

@dataclass
class StubEvents:
    log: list[tuple[Any, dict[str, Any], str | None]] = field(default_factory=list)

    def append(
        self, kind: Any, payload: dict[str, Any], *, actor: str | None = None,
        correlation: str | None = None,
    ) -> None:
        self.log.append((kind, payload, actor))


@dataclass
class StubIdentity:
    agents: list[StubAgent]

    def all(self, status: Status | None = None) -> list[StubAgent]:
        if status is None:
            return list(self.agents)
        return [a for a in self.agents if a.status == status]


@dataclass
class StubOrchestrator:
    delivered: list[tuple[str, str, MessageKind, str | None]] = field(default_factory=list)
    paused: bool = False
    next_result: SendResult | None = None

    async def system_send(
        self,
        to_agent: str,
        content: str,
        kind: MessageKind = MessageKind.NOTIFY,
        *,
        correlation_id: str | None = None,
    ) -> SendResult:
        if self.next_result is not None:
            res = self.next_result
            self.next_result = None
            if res is SendResult.QUEUED:
                self.delivered.append((to_agent, content, kind, correlation_id))
            return res
        if self.paused:
            return SendResult.REJECTED_PAUSED
        self.delivered.append((to_agent, content, kind, correlation_id))
        return SendResult.QUEUED


@dataclass
class StubHandle:
    company_id: str
    identity: StubIdentity
    orchestrator: StubOrchestrator
    events: StubEvents = field(default_factory=StubEvents)


@dataclass
class StubSummary:
    company_id: str
    status: CompanyStatus = CompanyStatus.ACTIVE


@dataclass
class StubFactory:
    handles: dict[str, StubHandle] = field(default_factory=dict)
    statuses: dict[str, CompanyStatus] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)

    def add_company(
        self,
        company_id: str,
        agents: list[StubAgent],
        *,
        status: CompanyStatus = CompanyStatus.ACTIVE,
        paused: bool = False,
    ) -> StubHandle:
        handle = StubHandle(
            company_id=company_id,
            identity=StubIdentity(agents=agents),
            orchestrator=StubOrchestrator(paused=paused),
        )
        self.handles[company_id] = handle
        self.statuses[company_id] = status
        return handle

    def close_company(self, company_id: str) -> None:
        self.statuses[company_id] = CompanyStatus.CLOSED

    # CompanyFactory-compatible surface ---------------------------------

    def get_summary(self, company_id: str) -> StubSummary:
        if company_id in self.missing or company_id not in self.statuses:
            raise KeyError(company_id)
        return StubSummary(
            company_id=company_id, status=self.statuses[company_id],
        )

    def get_handle(self, company_id: str) -> StubHandle:
        if company_id in self.missing or company_id not in self.handles:
            raise KeyError(company_id)
        return self.handles[company_id]


# --------------------------------------------------------- pytest setup

@pytest.fixture
def board_db(tmp_path: Path) -> Iterator[BoardDB]:
    db = BoardDB.init(tmp_path)
    db.migrate()
    # Apply the board_api schema (provides board_links table).
    apply_migrations(
        db.connect(),
        module="board_api",
        migrations=load_migrations_from_package("modules.board_api.migrations"),
    )
    yield db


@pytest.fixture
def factory() -> StubFactory:
    f = StubFactory()
    f.add_company("co_a", [StubAgent("a_hr", Role.HR), StubAgent("a_ceo", Role.CEO)])
    f.add_company("co_b", [StubAgent("b_hr", Role.HR), StubAgent("b_ceo", Role.CEO)])
    return f


@pytest.fixture
def links(board_db: BoardDB, factory: StubFactory) -> LinkService:
    # LinkService validates company existence via factory.get_summary.
    return LinkService(board_db, factory)  # type: ignore[arg-type]


@pytest.fixture
def service(
    board_db: BoardDB, factory: StubFactory, links: LinkService,
) -> InterCompanyService:
    s = InterCompanyService(
        board_db=board_db, factory=factory, links=links,  # type: ignore[arg-type]
    )
    s.migrate()
    return s


@pytest.fixture
def approved_link(
    links: LinkService, board_db: BoardDB,
) -> Any:
    """Create A->B link in APPROVED state with default scope."""
    link = links.request(
        from_company="co_a",
        to_company="co_b",
        relationship=LinkRelationship.PEER,
        scope=LinkScope(
            allowed_messages=("inquiry", "quote", "order", "generic"),
            rate_limit_per_hour=100,
            max_payload_bytes=8192,
        ),
        requested_by="u_admin",
    )
    return links.decide(
        link.id, decision="approve", decided_by="u_admin", note=None,
    )


@pytest.fixture
def run_async() -> Any:
    """Helper: run a coroutine synchronously in tests."""

    def _run(coro: Any) -> Any:
        return asyncio.get_event_loop().run_until_complete(coro)

    # asyncio.get_event_loop() is deprecated in 3.12 for getting a running loop;
    # supply a fresh loop.
    def _runner(coro: Any) -> Any:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    return _runner


__all__ = [
    "StubAgent",
    "StubFactory",
    "StubHandle",
    "StubIdentity",
    "StubOrchestrator",
    "StubSummary",
]
