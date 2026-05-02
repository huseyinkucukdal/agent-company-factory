"""Budget charging + thresholds + concurrency."""
from __future__ import annotations

from decimal import Decimal

from modules.cost import Budget, Category
from modules.event_store import EventKind, EventStore

from .conftest import usd


def _events_of_kind(es: EventStore, kind: EventKind) -> list[object]:
    return [e for e in es.read() if e.kind == kind]


def test_charge_within_budget_succeeds(
    budget: Budget, event_store: EventStore
) -> None:
    budget.set_total(usd(100))
    res = budget.charge(
        agent_id="alice",
        amount=usd("12.50"),
        category=Category.LLM,
        memo="prompt batch",
    )
    assert res.ok is True
    assert res.expense_id is not None

    state = budget.state()
    assert state.spent == usd("12.50")
    assert state.remaining == usd("87.50")
    assert state.by_category[Category.LLM] == usd("12.50")
    assert _events_of_kind(event_store, EventKind.EXPENSE_CHARGED)


def test_charge_over_budget_returns_over_budget(
    budget: Budget, event_store: EventStore
) -> None:
    budget.set_total(usd(10))
    over = budget.charge(
        agent_id="a",
        amount=usd(15),
        category=Category.TOOL,
        memo="too big",
    )
    assert over.ok is False
    assert over.over_budget is True
    # Nothing recorded as spent.
    assert budget.state().spent == usd(0)
    assert _events_of_kind(event_store, EventKind.BUDGET_BLOCKED)
    # Subsequent charges blocked.
    again = budget.charge(
        agent_id="a", amount=usd("0.01"), category=Category.TOOL, memo="x"
    )
    assert again.ok is False


def test_warning_event_at_80_pct(
    budget: Budget, event_store: EventStore
) -> None:
    budget.set_total(usd(100))
    budget.charge(
        agent_id="a", amount=usd(60), category=Category.LLM, memo="m"
    )
    assert _events_of_kind(event_store, EventKind.BUDGET_WARNING) == []
    budget.charge(
        agent_id="a", amount=usd(20), category=Category.LLM, memo="m"
    )
    warns = _events_of_kind(event_store, EventKind.BUDGET_WARNING)
    assert len(warns) == 1
    # Crossing again above 80% must not re-emit.
    budget.charge(
        agent_id="a", amount=usd("5"), category=Category.LLM, memo="m"
    )
    assert len(_events_of_kind(event_store, EventKind.BUDGET_WARNING)) == 1


def test_blocked_at_100_pct(budget: Budget, event_store: EventStore) -> None:
    budget.set_total(usd(50))
    budget.charge(
        agent_id="a", amount=usd(50), category=Category.LLM, memo="m"
    )
    blocks = _events_of_kind(event_store, EventKind.BUDGET_BLOCKED)
    assert len(blocks) == 1
    assert budget.state().blocked is True
    # Now even tiny charges fail.
    res = budget.charge(
        agent_id="a", amount=usd("0.01"), category=Category.LLM, memo="m"
    )
    assert res.ok is False


def test_blocked_after_total_increased_unblocks(
    budget: Budget, event_store: EventStore
) -> None:
    budget.set_total(usd(10))
    budget.charge(
        agent_id="a", amount=usd(10), category=Category.LLM, memo="m"
    )
    assert budget.state().blocked is True
    budget.set_total(usd(100))
    state = budget.state()
    assert state.blocked is False
    res = budget.charge(
        agent_id="a", amount=usd(5), category=Category.LLM, memo="m"
    )
    assert res.ok is True


def test_decimal_precision_no_float_drift(budget: Budget) -> None:
    budget.set_total(usd(1))
    for _ in range(10):
        ok = budget.charge(
            agent_id="a",
            amount=usd("0.1000"),
            category=Category.LLM,
            memo="d",
        ).ok
        assert ok
    s = budget.state()
    assert s.spent.amount_usd == Decimal("1.0000")
    assert s.remaining.amount_usd == Decimal("0.0000")
    # 11th charge of any positive amount must hit blocked.
    res = budget.charge(
        agent_id="a", amount=usd("0.0001"), category=Category.LLM, memo="d"
    )
    assert res.ok is False


def test_can_afford_respects_reserved(budget: Budget) -> None:
    budget.set_total(usd(10))
    budget.reserve(usd(7), ref="x")
    assert budget.can_afford(usd(3)) is True
    assert budget.can_afford(usd("3.0001")) is False


def test_concurrent_charges_no_race(budget: Budget) -> None:
    """16 threads each charge $1.00 against a $16 budget — all must succeed
    and the totals must be exact."""
    import threading

    budget.set_total(usd(16))
    barrier = threading.Barrier(16)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            res = budget.charge(
                agent_id="t",
                amount=usd(1),
                category=Category.TOOL,
                memo="parallel",
            )
            assert res.ok
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert budget.state().spent == usd(16)
