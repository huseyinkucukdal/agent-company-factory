"""Shared fixtures + fakes for Identity tests."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from modules.event_store import EventStore
from modules.identity import ApprovalDecision, Org, Role
from modules.storage import CompanyDB


@dataclass
class FakeApprovalRequester:
    """Records every fire-approval request without executing it."""

    requests: list[dict[str, str]] = field(default_factory=list)

    def request_fire(
        self,
        *,
        request_id: str,
        actor_id: str,
        target_id: str,
        decider_id: str,
        reason: str,
    ) -> None:
        self.requests.append(
            {
                "request_id": request_id,
                "actor_id": actor_id,
                "target_id": target_id,
                "decider_id": decider_id,
                "reason": reason,
            }
        )

    def last(self) -> dict[str, str]:
        assert self.requests, "no approval request recorded"
        return self.requests[-1]


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def company_db(storage_root: Path) -> Iterator[CompanyDB]:
    db = CompanyDB.init("acme", storage_root)
    db.migrate()
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
def approvals() -> FakeApprovalRequester:
    return FakeApprovalRequester()


@pytest.fixture
def org(
    company_db: CompanyDB,
    event_store: EventStore,
    approvals: FakeApprovalRequester,
) -> Org:
    o = Org(company_db, event_store, approvals)
    o.migrate()
    return o


# ---------------------------------------------------------- helper builders


def bootstrap_ceo(org: Org) -> str:
    a = org.add_agent(
        role=Role.CEO,
        persona_ref="ceo.v1",
        reports_to=None,
        requested_by="board",
        via_hr=False,
        bootstrap=True,
    )
    return a.id


def bootstrap_hr(org: Org, ceo_id: str) -> str:
    a = org.add_agent(
        role=Role.HR,
        persona_ref="hr.v1",
        reports_to=ceo_id,
        requested_by="board",
        via_hr=False,
        bootstrap=True,
    )
    return a.id


def hire(
    org: Org, role: Role, manager: str | None, *, by: str = "hr"
) -> str:
    a = org.add_agent(
        role=role,
        persona_ref=f"{role.value}.v1",
        reports_to=manager,
        requested_by=by,
        via_hr=True,
    )
    return a.id


# Re-exported decisions for convenient access in tests.
APPROVED = ApprovalDecision.APPROVED
DECLINED = ApprovalDecision.DENIED
