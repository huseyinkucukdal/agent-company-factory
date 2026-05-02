"""Subscription DAY_TICK consumption."""
from __future__ import annotations

from modules.cost import Budget, Category, Subscriptions
from modules.event_store import EventKind, EventStore

from .conftest import usd


def test_subscription_charged_on_day_tick(
    budget: Budget,
    subscriptions: Subscriptions,
    event_store: EventStore,
) -> None:
    budget.set_total(usd(100))
    sub = subscriptions.add(
        description="domain.com",
        amount=usd(10),
        category=Category.DOMAIN,
        next_charge_company_day=30,
        period_days=30,
    )

    # Day 29 — not yet due.
    subscriptions.on_day_tick(29)
    assert budget.state().spent == usd(0)

    subscriptions.on_day_tick(30)
    assert budget.state().spent == usd(10)
    assert subscriptions.all()[0].next_charge_company_day == 60
    assert sub.id == subscriptions.all()[0].id

    # Day 60 — second charge.
    subscriptions.on_day_tick(60)
    assert budget.state().spent == usd(20)


def test_subscription_blocked_when_budget_full(
    budget: Budget,
    subscriptions: Subscriptions,
    event_store: EventStore,
) -> None:
    budget.set_total(usd(5))
    subscriptions.add(
        description="hosting",
        amount=usd(50),
        category=Category.HOSTING,
        next_charge_company_day=0,
    )

    subscriptions.on_day_tick(0)
    blocks = [e for e in event_store.read() if e.kind == EventKind.BUDGET_BLOCKED]
    assert len(blocks) >= 1
    # Subscription is now cancelled; further ticks must not retry.
    assert subscriptions.all() == []
    cancelled = subscriptions.all(include_cancelled=True)
    assert len(cancelled) == 1
    assert cancelled[0].cancelled is True

    subscriptions.on_day_tick(100)
    assert budget.state().spent == usd(0)


def test_remove_cancels(
    subscriptions: Subscriptions,
) -> None:
    s = subscriptions.add(
        description="x",
        amount=usd(1),
        category=Category.OTHER,
        next_charge_company_day=0,
    )
    subscriptions.remove(s.id)
    assert subscriptions.all() == []


def test_due_lists_only_unpaid(
    subscriptions: Subscriptions,
) -> None:
    a = subscriptions.add(
        description="a",
        amount=usd(1),
        category=Category.OTHER,
        next_charge_company_day=10,
    )
    b = subscriptions.add(
        description="b",
        amount=usd(1),
        category=Category.OTHER,
        next_charge_company_day=20,
    )
    due_at_15 = {s.id for s in subscriptions.due(15)}
    assert due_at_15 == {a.id}
    due_at_25 = {s.id for s in subscriptions.due(25)}
    assert due_at_25 == {a.id, b.id}
