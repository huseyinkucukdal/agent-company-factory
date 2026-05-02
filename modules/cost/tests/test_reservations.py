"""Reservation / commit / release tests."""
from __future__ import annotations

import pytest

from modules.cost import Budget, OverBudget
from modules.cost.exceptions import (
    ReservationAlreadyClosed,
    UnknownReservation,
)

from .conftest import usd


def test_reserve_release_does_not_consume(budget: Budget) -> None:
    budget.set_total(usd(10))
    r = budget.reserve(usd(4), ref="probe")
    state = budget.state()
    assert state.reserved == usd(4)
    assert state.spent == usd(0)
    assert state.remaining == usd(6)

    budget.release(r.id)
    state = budget.state()
    assert state.reserved == usd(0)
    assert state.remaining == usd(10)


def test_reserve_commit_actual_lower_than_estimate(budget: Budget) -> None:
    budget.set_total(usd(10))
    r = budget.reserve(usd(5), ref="probe")
    res = budget.commit(r.id, actual_amount=usd(3))
    assert res.ok is True
    state = budget.state()
    assert state.spent == usd(3)
    assert state.reserved == usd(0)
    assert state.remaining == usd(7)


def test_reserve_commit_actual_higher_than_estimate(budget: Budget) -> None:
    budget.set_total(usd(10))
    r = budget.reserve(usd(2), ref="probe")
    # Actual exceeds estimate but still within remaining headroom.
    res = budget.commit(r.id, actual_amount=usd(5))
    assert res.ok is True
    state = budget.state()
    assert state.spent == usd(5)
    assert state.reserved == usd(0)


def test_reserve_refused_when_blocked(budget: Budget) -> None:
    budget.set_total(usd(2))
    budget.charge(agent_id="a", amount=usd(2), category=__import__(
        "modules.cost", fromlist=["Category"]).Category.LLM, memo="x")
    assert budget.state().blocked is True
    with pytest.raises(OverBudget):
        budget.reserve(usd("0.01"), ref="r")


def test_reserve_refused_when_total_exceeded(budget: Budget) -> None:
    budget.set_total(usd(5))
    budget.reserve(usd(3), ref="a")
    with pytest.raises(OverBudget):
        budget.reserve(usd(3), ref="b")


def test_commit_unknown_reservation_raises(budget: Budget) -> None:
    with pytest.raises(UnknownReservation):
        budget.commit("nope", actual_amount=usd(1))


def test_commit_already_closed_raises(budget: Budget) -> None:
    budget.set_total(usd(10))
    r = budget.reserve(usd(2), ref="x")
    budget.release(r.id)
    with pytest.raises(ReservationAlreadyClosed):
        budget.commit(r.id, actual_amount=usd(1))


def test_commit_actual_exceeds_budget_blocks(budget: Budget) -> None:
    budget.set_total(usd(5))
    r = budget.reserve(usd(2), ref="x")
    # No other charges → headroom 5; actual=10 must be refused.
    res = budget.commit(r.id, actual_amount=usd(10))
    assert res.ok is False
    state = budget.state()
    assert state.blocked is True
    assert state.spent == usd(0)


def test_open_reservations_listing(budget: Budget) -> None:
    budget.set_total(usd(10))
    a = budget.reserve(usd(1), ref="a")
    b = budget.reserve(usd(2), ref="b")
    budget.release(a.id)
    open_ids = [r.id for r in budget.reservations()]
    assert open_ids == [b.id]
