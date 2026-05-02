"""Connector test fixtures."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import pytest

from modules.approvals import Approvals
from modules.connector import (
    Allowlist,
    Connector,
    EncryptedSqliteSecrets,
    RateLimiter,
    in_memory_secrets,
)
from modules.cost import Budget, Money
from modules.cost import migrate as cost_migrate
from modules.event_store import EventStore
from modules.storage import CompanyDB


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.t

    def advance(self, **kwargs: float) -> None:
        self.t += timedelta(**kwargs)


class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


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
def allowlist() -> Allowlist:
    return Allowlist()


@pytest.fixture
def fake_monotonic() -> FakeMonotonic:
    return FakeMonotonic()


@pytest.fixture
def rate_limiter(fake_monotonic: FakeMonotonic) -> RateLimiter:
    return RateLimiter(time_provider=fake_monotonic)


@pytest.fixture
def secrets() -> Any:
    return in_memory_secrets()


@pytest.fixture
def connector(
    budget: Budget, approvals: Approvals, event_store: EventStore,
    secrets: Any, allowlist: Allowlist, rate_limiter: RateLimiter,
) -> Connector:
    return Connector(
        cost=budget,
        approvals=approvals,
        events=event_store,
        secrets=secrets,
        allowlist=allowlist,
        rate_limiter=rate_limiter,
    )


@dataclass
class FakeHttpResponse:
    status: int
    headers: dict[str, str]
    text: str


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.next_response: FakeHttpResponse = FakeHttpResponse(
            status=200, headers={"content-type": "text/plain"}, text="hello"
        )
        self.fail_first_n: int = 0
        self._failures = 0

    def request(
        self, method: str, url: str, *,
        headers: Any, body: Any, timeout: float,
    ) -> FakeHttpResponse:
        self.calls.append((method, url))
        if self._failures < self.fail_first_n:
            self._failures += 1
            from modules.connector.exceptions import ExternalServiceFailure
            raise ExternalServiceFailure("transient")
        return self.next_response


@pytest.fixture
def http_client() -> FakeHttpClient:
    return FakeHttpClient()


class FakeEmailResponse:
    message_id = "m-1"
    accepted: ClassVar[list[str]] = ["a@example.com"]


class FakeEmailClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, **kwargs: Any) -> FakeEmailResponse:
        self.sent.append(kwargs)
        return FakeEmailResponse()


@pytest.fixture
def email_client() -> FakeEmailClient:
    return FakeEmailClient()


@pytest.fixture
def encrypted_secrets(company_db: CompanyDB) -> EncryptedSqliteSecrets:
    key = EncryptedSqliteSecrets.generate_master_key()
    return EncryptedSqliteSecrets(company_db, key)
