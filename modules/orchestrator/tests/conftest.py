"""Test fixtures for the Orchestrator."""
from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from modules.agent_runtime import (
    AgentStatus,
    HealthSignals,
    IncomingMessage,
)
from modules.cost import migrate as cost_migrate
from modules.event_store import EventStore
from modules.identity import Org, Role, Status
from modules.orchestrator import (
    InMemoryMessageQueue,
    MessageKind,
    Orchestrator,
    RateLimitConfig,
)
from modules.storage import CompanyDB

# --------------------------------------------------------- support adapters


class _NoopFire:
    def request_fire(self, **_kwargs: Any) -> None:
        return None


class FakeClock:
    """Minimal Clock surface needed by the Orchestrator."""

    def __init__(self) -> None:
        self.t = datetime(2026, 1, 1, tzinfo=UTC)
        self._accepting = True

    def now_company(self) -> datetime:
        return self.t

    def is_accepting_messages(self) -> bool:
        return self._accepting

    def pause_sync(self) -> None:
        self._accepting = False

    def resume_sync(self) -> None:
        self._accepting = True

    def advance(self, **kwargs: float) -> None:
        self.t += timedelta(**kwargs)


class FakeAgent:
    """Minimal :class:`AgentHandle` impl that records deliveries."""

    def __init__(
        self, agent_id: str, *, now: datetime | None = None,
    ) -> None:
        self._id = agent_id
        self.delivered: list[IncomingMessage] = []
        self._status: AgentStatus = AgentStatus.IDLE
        self.fail_n = 0
        self._signals = HealthSignals(
            last_heartbeat=now or datetime(2026, 1, 1, tzinfo=UTC),
        )

    @property
    def id(self) -> str:
        return self._id

    @property
    def inbox_size(self) -> int:
        return 0

    def status(self) -> AgentStatus:
        return self._status

    def set_status(self, s: AgentStatus) -> None:
        self._status = s

    def health_signals(self) -> HealthSignals:
        return self._signals

    def set_signals(self, **fields: Any) -> None:
        self._signals = HealthSignals(
            last_heartbeat=fields.get("last_heartbeat", self._signals.last_heartbeat),
            consecutive_errors=fields.get("consecutive_errors", self._signals.consecutive_errors),
            parse_failures=fields.get("parse_failures", self._signals.parse_failures),
            repeat_response_count=fields.get("repeat_response_count", self._signals.repeat_response_count),
            avg_turn_seconds=fields.get("avg_turn_seconds", self._signals.avg_turn_seconds),
            total_turns=fields.get("total_turns", self._signals.total_turns),
        )

    async def deliver(self, message: IncomingMessage) -> None:
        if self.fail_n > 0:
            self.fail_n -= 1
            raise RuntimeError("scripted delivery failure")
        self.delivered.append(message)


# --------------------------------------------------------------- fixtures


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def company_db(storage_root: Path) -> Iterator[CompanyDB]:
    db = CompanyDB.init("acme", storage_root)
    db.migrate()
    cost_migrate(db)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def event_store(company_db: CompanyDB) -> EventStore:
    es = EventStore(company_db)
    es.migrate()
    return es


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def org(company_db: CompanyDB, event_store: EventStore) -> Org:
    o = Org(company_db, event_store, _NoopFire())
    o.migrate()
    return o


@pytest.fixture
def bootstrap_org(org: Org) -> dict[str, str]:
    """Create CEO + HR + engineer (eng reports to CEO)."""
    ceo = org.add_agent(
        role=Role.CEO, persona_ref="ceo.v1",
        reports_to=None, requested_by="BOOTSTRAP",
        via_hr=False, bootstrap=True,
    )
    hr = org.add_agent(
        role=Role.HR, persona_ref="hr.v1",
        reports_to=ceo.id, requested_by="BOOTSTRAP",
        via_hr=False, bootstrap=True,
    )
    eng = org.add_agent(
        role=Role.MEMBER, persona_ref="eng.v1",
        reports_to=ceo.id, requested_by=hr.id, via_hr=True,
    )
    return {"ceo": ceo.id, "hr": hr.id, "eng": eng.id}


@pytest.fixture
def queue() -> InMemoryMessageQueue:
    return InMemoryMessageQueue()


@pytest.fixture
def orch(
    org: Org, event_store: EventStore, clock: FakeClock,
    queue: InMemoryMessageQueue, bootstrap_org: dict[str, str],
) -> Orchestrator:
    o = Orchestrator(
        "acme",
        identity=org, events=event_store, clock=clock,  # type: ignore[arg-type]
        queue=queue,
        rate_limit=RateLimitConfig(
            capacity=3, refill_per_second=0.0, system_exempt=True,
        ),
        now=clock.now_company,
        monotonic=time.monotonic,
    )
    for aid in bootstrap_org.values():
        o.register(FakeAgent(aid, now=clock.now_company()))
    return o


@pytest.fixture
def msg_kind() -> type[MessageKind]:
    return MessageKind


@pytest.fixture
def status_enum() -> type[Status]:
    return Status
