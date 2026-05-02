"""Shared fixtures for the Security Agent test suite."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from modules.approvals import Approvals
from modules.cost import migrate as cost_migrate
from modules.event_store import EventStore
from modules.identity import Org, Role
from modules.security_agent import (
    SecurityDeps,
    SecurityPolicy,
    StaticRuleSet,
)
from modules.security_agent.static_rules import RuleContext, default_context
from modules.storage import CompanyDB

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class _NoopFire:
    def request_fire(self, **_kwargs: Any) -> None:
        return None


class FakeOrch:
    def __init__(self) -> None:
        self.suspended: list[tuple[str, str]] = []

    def suspend(self, agent_id: str, *, reason: str = "security") -> None:
        self.suspended.append((agent_id, reason))


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def db(storage_root: Path) -> Iterator[CompanyDB]:
    d = CompanyDB.init("acme", storage_root)
    d.migrate()
    cost_migrate(d)
    try:
        yield d
    finally:
        d.close()


@pytest.fixture
def events(db: CompanyDB) -> EventStore:
    es = EventStore(db, company_now=lambda: T0)
    es.migrate()
    return es


@pytest.fixture
def approvals(db: CompanyDB, events: EventStore) -> Approvals:
    a = Approvals(db, events, time_provider=lambda: T0)
    a.migrate()
    return a


@pytest.fixture
def org(db: CompanyDB, events: EventStore) -> Org:
    o = Org(db, events, _NoopFire())
    o.migrate()
    return o


@pytest.fixture
def bootstrap(org: Org) -> dict[str, str]:
    ceo = org.add_agent(
        role=Role.CEO, persona_ref="ceo.v1", reports_to=None,
        requested_by="BOOT", via_hr=False, bootstrap=True,
    )
    hr = org.add_agent(
        role=Role.HR, persona_ref="hr.v1", reports_to=ceo.id,
        requested_by="BOOT", via_hr=False, bootstrap=True,
    )
    sec = org.add_agent(
        role=Role.SECURITY, persona_ref="sec.v1", reports_to=ceo.id,
        requested_by=hr.id, via_hr=True,
    )
    eng = org.add_agent(
        role=Role.MEMBER, persona_ref="eng.v1", reports_to=ceo.id,
        requested_by=hr.id, via_hr=True,
    )
    return {"ceo": ceo.id, "hr": hr.id, "sec": sec.id, "eng": eng.id}


@pytest.fixture
def orch() -> FakeOrch:
    return FakeOrch()


@pytest.fixture
def rules() -> StaticRuleSet:
    return StaticRuleSet()


@pytest.fixture
def ctx_factory() -> Any:
    def _factory() -> RuleContext:
        c = default_context(budget_total_cents=10_000)
        return c
    return _factory


@pytest.fixture
def policy(
    approvals: Approvals, events: EventStore, orch: FakeOrch,
    rules: StaticRuleSet, ctx_factory: Any, bootstrap: dict[str, str],
) -> SecurityPolicy:
    deps = SecurityDeps(
        approvals=approvals,
        events=events,
        orchestrator=orch,
        rules=rules,
        rule_context_factory=ctx_factory,
        now=lambda: T0 + timedelta(seconds=1),
    )
    return SecurityPolicy(bootstrap["sec"], deps)
