"""Shared fixtures for the Agent Runtime."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from modules.agent_runtime import (
    Agent,
    AgentDeps,
    InMemoryPersonaLoader,
    LLMClient,
    TurnEvent,
    default_persona_loader,
)
from modules.approvals import Approvals
from modules.cost import Budget, Money
from modules.cost import migrate as cost_migrate
from modules.event_store import EventStore
from modules.identity import Agent as IdentityAgent
from modules.identity import Org, Role, Status
from modules.memory import FakeEmbedder, Memory, WorkingItem
from modules.storage import CompanyDB, Workspace
from modules.storage.quota import CompanyQuota, Quota
from modules.tools import Tools, register_builtins

# --- adapters re-used from the tools test suite ---------------------------


class _NoopFireRequester:
    def request_fire(self, **_kwargs: Any) -> None:
        return None


class _StubMemoryIdentity:
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
    def __init__(self, org: Org) -> None:
        self._org = org

    def can_read_workspace(self, reader_id: str, owner_id: str) -> bool:
        return self._org.can_read_workspace(reader_id, owner_id)


class _StorageEventSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 1, 1, tzinfo=UTC)
        self._accepting = True

    def now(self) -> datetime:
        return self.t

    def advance(self, **kwargs: float) -> None:
        self.t += timedelta(**kwargs)

    def is_accepting_messages(self) -> bool:
        return self._accepting

    def pause_sync(self) -> None:
        self._accepting = False

    def resume_sync(self) -> None:
        self._accepting = True


# --- LLM script-driver -----------------------------------------------------


class FakeLLM:
    """Scriptable LLM. Each ``run_turn`` call pops a list of TurnEvents."""

    def __init__(self) -> None:
        self.scripts: list[list[TurnEvent]] = []
        self.run_calls: list[dict[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []
        self.completion_replies: list[str] = []
        self.fail_run_turn_n: int = 0

    def push(self, events: Sequence[TurnEvent]) -> None:
        self.scripts.append(list(events))

    def push_text(self, text: str) -> None:
        self.push([TurnEvent(type="text", text=text), TurnEvent(type="stop")])

    def run_turn(
        self, *, system: str, history: list[WorkingItem],
        tool_schemas: list[dict[str, Any]],
        cache_keys: list[str] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        self.run_calls.append({
            "system": system,
            "history": list(history),
            "tool_schemas": list(tool_schemas),
            "cache_keys": list(cache_keys or []),
        })
        if self.fail_run_turn_n > 0:
            self.fail_run_turn_n -= 1
            raise TimeoutError("scripted timeout")
        events = self.scripts.pop(0) if self.scripts else [TurnEvent(type="stop")]

        async def _gen() -> AsyncIterator[TurnEvent]:
            for ev in events:
                yield ev

        return _gen()

    async def submit_tool_result(
        self, *, tool_use_id: str, ok: bool,
        output: Mapping[str, Any] | str,
    ) -> None:
        self.tool_results.append(
            {"tool_use_id": tool_use_id, "ok": ok, "output": output}
        )

    async def completion(
        self, prompt: str, *, context: list[WorkingItem]
    ) -> str:
        if self.completion_replies:
            return self.completion_replies.pop(0)
        return "auto-summary"


# --- pytest fixtures -------------------------------------------------------


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
    o = Org(company_db, event_store, _NoopFireRequester())
    o.migrate()
    return o


@pytest.fixture
def approvals(
    company_db: CompanyDB, event_store: EventStore, clock: FakeClock,
) -> Approvals:
    svc = Approvals(
        company_db, event_store,
        time_provider=clock.now,
        require_security_kinds=frozenset(),
    )
    svc.migrate()
    return svc


@pytest.fixture
def budget(company_db: CompanyDB, event_store: EventStore) -> Budget:
    b = Budget(company_db, event_store)
    b.set_total(Money.of(Decimal("1000.00")))
    return b


@pytest.fixture
def workspace(company_db: CompanyDB, org: Org) -> Workspace:
    Quota(company_db).set_limit(CompanyQuota(), mb=10)
    return Workspace(company_db, _StubStorageIdentity(org), _StorageEventSink())


@pytest.fixture
def memory(company_db: CompanyDB, org: Org) -> Memory:
    m = Memory(company_db, _StubMemoryIdentity(org), FakeEmbedder(dim=4))
    m.migrate()
    return m


class _FakeConnector:
    async def call(self, service: str, endpoint: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"echo": args, "service": service, "endpoint": endpoint}


@pytest.fixture
def tools(
    org: Org, budget: Budget, approvals: Approvals,
    workspace: Workspace, memory: Memory, event_store: EventStore,
) -> Tools:
    t = Tools(
        identity=org,
        cost=budget,
        approvals=approvals,
        workspace=workspace,
        memory=memory,
        events=event_store,
        connector=_FakeConnector(),
    )
    register_builtins(t)
    return t


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def persona_loader() -> InMemoryPersonaLoader:
    return default_persona_loader()


@pytest.fixture
def ceo(org: Org, workspace: Workspace) -> IdentityAgent:
    a = org.add_agent(
        role=Role.CEO,
        persona_ref="ceo.v1",
        reports_to=None,
        requested_by="BOOTSTRAP",
        via_hr=False,
        bootstrap=True,
    )
    workspace.create_for_agent(a.id, quota_mb=1)
    return a


@pytest.fixture
def deps(
    org: Org, memory: Memory, tools: Tools, event_store: EventStore,
    budget: Budget, clock: FakeClock, llm: FakeLLM,
    persona_loader: InMemoryPersonaLoader,
) -> AgentDeps:
    return AgentDeps(
        identity=org,
        memory=memory,
        tools=tools,
        events=event_store,
        cost=budget,
        clock=clock,  # type: ignore[arg-type]
        llm=llm,
        persona_loader=persona_loader,
        company_id="acme",
        company_mission="ship great products",
    )


@pytest.fixture
def agent(ceo: IdentityAgent, deps: AgentDeps) -> Agent:
    return Agent(ceo.id, deps)


@pytest.fixture
def llm_protocol_check(llm: FakeLLM) -> bool:
    return isinstance(llm, LLMClient)
