"""Shared fixtures for the Tool Layer."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from modules.approvals import Approvals
from modules.cost import Budget, Money
from modules.cost import migrate as cost_migrate
from modules.event_store import EventStore
from modules.identity import Agent, Org, Role, Status
from modules.memory import FakeEmbedder, Memory
from modules.storage import CompanyDB, PROJECT_WORKSPACE_ID, Workspace
from modules.storage.quota import CompanyQuota, Quota
from modules.tools import Tools, register_builtins


class _NoopFireRequester:
    def request_fire(self, **kwargs: Any) -> None:
        return None


class _StubIdentityForMemory:
    """Adapter Memory expects (is_active + can_read_workspace)."""

    def __init__(self, org: Org) -> None:
        self._org = org

    def is_active(self, agent_id: str) -> bool:
        try:
            return self._org.get(agent_id).status is Status.ACTIVE
        except KeyError:
            return False

    def can_read_workspace(self, reader_id: str, owner_id: str) -> bool:
        return self._org.can_read_workspace(reader_id, owner_id)


class _StubStorageIdentity:
    """Adapter Workspace expects (can_read_workspace)."""

    def __init__(self, org: Org) -> None:
        self._org = org

    def can_read_workspace(self, reader_id: str, owner_id: str) -> bool:
        return self._org.can_read_workspace(reader_id, owner_id)


class _StorageEventSink:
    """Collect Storage events. Tools tests don't need to mirror them into the
    main event log; they only assert on the Tool Layer's own emissions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.t

    def advance(self, **kwargs: float) -> None:
        self.t += timedelta(**kwargs)


class FakeConnector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(
        self, service: str, endpoint: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((service, endpoint, args))
        return {"echo": args, "service": service, "endpoint": endpoint}


class FakeHireService:
    def __init__(self, org: Org, workspace: Workspace) -> None:
        self._org = org
        self._workspace = workspace
        self.requests: list[dict[str, Any]] = []

    async def hire_member(
        self,
        *,
        requester_id: str,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        self.requests.append({"requester_id": requester_id, "payload": payload})
        agent = self._org.add_agent(
            role=Role.MEMBER,
            persona_ref="default",
            reports_to=payload.get("reports_to"),
            requested_by=requester_id,
            via_hr=True,
            first_name=payload.get("first_name", ""),
            last_name=payload.get("last_name", ""),
            role_title=payload.get("role_title"),
            role_description=payload.get("role_description"),
        )
        self._workspace.create_for_agent(agent.id, quota_mb=1)
        return {"agent_id": agent.id, "status": "hired"}


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
def approvals(
    company_db: CompanyDB, event_store: EventStore, clock: FakeClock
) -> Approvals:
    svc = Approvals(
        company_db,
        event_store,
        time_provider=clock.now,
        # Tool-layer tests focus on the tool→approval handshake; the
        # security pre-veto interplay is covered in approvals tests.
        require_security_kinds=frozenset(),
    )
    svc.migrate()
    return svc


@pytest.fixture
def org(company_db: CompanyDB, event_store: EventStore) -> Org:
    o = Org(company_db, event_store, _NoopFireRequester())
    o.migrate()
    return o


@pytest.fixture
def budget(company_db: CompanyDB, event_store: EventStore) -> Budget:
    b = Budget(company_db, event_store)
    b.set_total(Money.of(Decimal("1000.00")))
    return b


@pytest.fixture
def workspace(company_db: CompanyDB, event_store: EventStore, org: Org) -> Workspace:
    Quota(company_db).set_limit(CompanyQuota(), mb=10)
    return Workspace(
        company_db,
        _StubStorageIdentity(org),
        _StorageEventSink(),
    )


@pytest.fixture
def memory(
    company_db: CompanyDB, org: Org
) -> Memory:
    m = Memory(company_db, _StubIdentityForMemory(org), FakeEmbedder(dim=4))
    m.migrate()
    return m


@pytest.fixture
def connector() -> FakeConnector:
    return FakeConnector()


@pytest.fixture
def hire_service(org: Org, workspace: Workspace) -> FakeHireService:
    return FakeHireService(org, workspace)


@pytest.fixture
def agents(org: Org, workspace: Workspace) -> dict[str, Agent]:
    """Bootstrap CEO, HR, an engineer reporting to CEO; create their workspaces."""
    ceo = org.add_agent(
        role=Role.CEO,
        persona_ref="p:ceo",
        reports_to=None,
        requested_by="BOOTSTRAP",
        via_hr=False,
        bootstrap=True,
    )
    hr = org.add_agent(
        role=Role.HR,
        persona_ref="p:hr",
        reports_to=ceo.id,
        requested_by="BOOTSTRAP",
        via_hr=False,
        bootstrap=True,
    )
    eng = org.add_agent(
        role=Role.MEMBER,
        persona_ref="p:eng",
        reports_to=ceo.id,
        requested_by="BOOTSTRAP",
        via_hr=False,
        bootstrap=True,
        role_title="engineer",
    )
    cfo = org.add_agent(
        role=Role.MEMBER,
        persona_ref="p:cfo",
        reports_to=ceo.id,
        requested_by="BOOTSTRAP",
        via_hr=False,
        bootstrap=True,
        role_title="cfo",
    )
    for a in (ceo, hr, eng, cfo):
        workspace.create_for_agent(a.id, quota_mb=1)
    return {"ceo": ceo, "hr": hr, "eng": eng, "cfo": cfo}


@pytest.fixture
def tools(
    org: Org,
    budget: Budget,
    approvals: Approvals,
    workspace: Workspace,
    memory: Memory,
    event_store: EventStore,
    connector: FakeConnector,
    hire_service: FakeHireService,
) -> Tools:
    workspace.ensure_for_agent(PROJECT_WORKSPACE_ID, quota_mb=2)
    t = Tools(
        identity=org,
        cost=budget,
        approvals=approvals,
        workspace=workspace,
        memory=memory,
        events=event_store,
        connector=connector,
    )
    register_builtins(t)
    t.set_hire_service(hire_service)
    return t
