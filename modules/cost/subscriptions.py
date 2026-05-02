"""Recurring subscriptions consumed via Clock's ``DAY_TICK``.

Subscriptions charge themselves on or after their ``next_charge_company_day``.
On budget refusal a subscription is cancelled (PLAN.md decision: "missed
charges stay missed; Board manually re-activates").
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from modules.event_store import EventStore
from modules.storage import CompanyDB

from .budget import Budget
from .models import Category, Money, Subscription


def _new_id() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Subscriptions:
    """Per-company recurring-charge ledger."""

    def __init__(
        self, db: CompanyDB, budget: Budget, events: EventStore
    ) -> None:
        self._db = db
        self._budget = budget
        self._events = events

    # ------------------------------------------------------------------ CRUD

    def add(
        self,
        *,
        description: str,
        amount: Money,
        category: Category,
        next_charge_company_day: int,
        period_days: int = 30,
    ) -> Subscription:
        if period_days <= 0:
            raise ValueError("period_days must be positive")
        sub_id = _new_id()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO subscriptions "
                "(id, description, amount_units, category, "
                " next_charge_company_day, period_days, cancelled) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (
                    sub_id,
                    description,
                    amount.to_units(),
                    category.value,
                    next_charge_company_day,
                    period_days,
                ),
            )
        return Subscription(
            id=sub_id,
            description=description,
            amount_per_company_month=amount,
            category=category,
            next_charge_company_day=next_charge_company_day,
            period_days=period_days,
        )

    def remove(self, sub_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE subscriptions SET cancelled = 1 WHERE id = ?",
                (sub_id,),
            )

    def all(self, *, include_cancelled: bool = False) -> list[Subscription]:
        if include_cancelled:
            rows = self._db.connect().execute(
                "SELECT * FROM subscriptions"
            ).fetchall()
        else:
            rows = self._db.connect().execute(
                "SELECT * FROM subscriptions WHERE cancelled = 0"
            ).fetchall()
        return [self._row_to_sub(r) for r in rows]

    def due(self, day: int) -> list[Subscription]:
        rows = self._db.connect().execute(
            "SELECT * FROM subscriptions "
            "WHERE cancelled = 0 AND next_charge_company_day <= ? "
            "ORDER BY next_charge_company_day, id",
            (day,),
        ).fetchall()
        return [self._row_to_sub(r) for r in rows]

    # ------------------------------------------------------------- DAY_TICK

    def on_day_tick(self, day: int) -> None:
        """Charge every subscription whose ``next_charge_company_day <= day``.

        On a successful charge the next due day advances by ``period_days``.
        On budget refusal the subscription is cancelled and stays so until
        manually re-added.
        """
        for sub in self.due(day):
            result = self._budget.charge(
                agent_id=None,
                amount=sub.amount_per_company_month,
                category=sub.category,
                memo=f"subscription:{sub.description}",
            )
            if result.ok:
                with self._db.transaction() as conn:
                    conn.execute(
                        "UPDATE subscriptions "
                        "SET next_charge_company_day = ? WHERE id = ?",
                        (sub.next_charge_company_day + sub.period_days, sub.id),
                    )
            else:
                with self._db.transaction() as conn:
                    conn.execute(
                        "UPDATE subscriptions SET cancelled = 1 WHERE id = ?",
                        (sub.id,),
                    )

    # ------------------------------------------------------------- internals

    def _row_to_sub(self, row: object) -> Subscription:
        # row is sqlite3.Row but typed loosely so we can index it.
        return Subscription(
            id=row["id"],  # type: ignore[index]
            description=row["description"],  # type: ignore[index]
            amount_per_company_month=Money.from_units(
                int(row["amount_units"])  # type: ignore[index]
            ),
            category=Category(row["category"]),  # type: ignore[index]
            next_charge_company_day=int(
                row["next_charge_company_day"]  # type: ignore[index]
            ),
            period_days=int(row["period_days"]),  # type: ignore[index]
            cancelled=bool(row["cancelled"]),  # type: ignore[index]
        )
