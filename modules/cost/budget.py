"""Budget engine: charges, reservations, threshold events.

Concurrency: every state-changing method runs inside a single
``CompanyDB.transaction()`` (BEGIN IMMEDIATE) which serialises writers.
Threshold events (``BUDGET_WARNING`` / ``BUDGET_BLOCKED``) are emitted
**after** commit so a callback that raises cannot abort the underlying
write.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from modules.event_store import EventKind, EventStore
from modules.storage import CompanyDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .exceptions import (
    OverBudget,
    ReservationAlreadyClosed,
    UnknownReservation,
)
from .models import (
    BLOCKED_PCT,
    WARNING_PCT,
    BudgetState,
    Category,
    ChargeResult,
    Money,
    Reservation,
    ReservationStatus,
)

_MODULE_KEY = "cost"


def migrate(db: CompanyDB) -> None:
    """Apply the cost-engine schema. Safe to call multiple times."""
    migrations = load_migrations_from_package("modules.cost.migrations")
    apply_migrations(db.connect(), module=_MODULE_KEY, migrations=migrations)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class Budget:
    """Per-company budget, charges, and reservations."""

    def __init__(self, db: CompanyDB, events: EventStore) -> None:
        self._db = db
        self._events = events

    # ---------------------------------------------------------------- state

    def state(self) -> BudgetState:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT total_units, blocked FROM budget_state WHERE id = 1"
        ).fetchone()
        total_u = int(row["total_units"])
        blocked = bool(row["blocked"])

        spent_u = self._sum_expenses(conn)
        reserved_u = self._sum_open_reservations(conn)

        per_cat: dict[Category, Money] = {}
        cat_rows = conn.execute(
            "SELECT category, COALESCE(SUM(amount_units), 0) AS s "
            "FROM expenses GROUP BY category"
        ).fetchall()
        for r in cat_rows:
            per_cat[Category(r["category"])] = Money.from_units(int(r["s"]))

        total = Money.from_units(total_u)
        spent = Money.from_units(spent_u)
        reserved = Money.from_units(reserved_u)
        remaining_units = max(0, total_u - spent_u - reserved_u)
        remaining = Money.from_units(remaining_units)
        return BudgetState(
            total=total,
            spent=spent,
            reserved=reserved,
            remaining=remaining,
            by_category=per_cat,
            blocked=blocked,
        )

    def can_afford(self, amount: Money) -> bool:
        s = self.state()
        if s.blocked:
            return False
        return s.spent.to_units() + s.reserved.to_units() + amount.to_units() \
            <= s.total.to_units()

    # ------------------------------------------------------------- set_total

    def set_total(self, amount: Money | Decimal | float | int | str) -> None:
        new_total = amount if isinstance(amount, Money) else Money.of(amount)
        post: list[tuple[EventKind, dict[str, object]]] = []
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE budget_state SET total_units = ? WHERE id = 1",
                (new_total.to_units(),),
            )
            post = self._evaluate_thresholds_locked(conn)
        for kind, payload in post:
            self._events.append(kind, payload)

    # ---------------------------------------------------------------- charge

    def charge(
        self,
        *,
        agent_id: str | None,
        amount: Money,
        category: Category,
        memo: str,
        approval_id: str | None = None,
    ) -> ChargeResult:
        """Direct expense path. No reservation involved.

        Returns a ChargeResult; never raises on over-budget — callers decide
        whether to bubble. An ``EXPENSE_CHARGED`` event fires on success and
        threshold events fire on warning / block crossings.
        """
        if amount.to_units() < 0:
            raise ValueError("charge amount must be non-negative")

        post_events: list[tuple[EventKind, dict[str, object]]] = []
        ts_company = _utcnow()  # company-time provided by caller via events
        ts_real = _utcnow()
        expense_id = _new_id()
        ok: bool

        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT total_units, blocked FROM budget_state WHERE id = 1"
            ).fetchone()
            total_u = int(row["total_units"])
            blocked = bool(row["blocked"])
            spent_u = self._sum_expenses(conn)
            reserved_u = self._sum_open_reservations(conn)

            if blocked:
                ok = False
                post_events.append(
                    (
                        EventKind.BUDGET_BLOCKED,
                        {
                            "scope": "company",
                            "used_cents": _units_to_cents(spent_u),
                            "limit_cents": _units_to_cents(total_u),
                            "attempted_cents": _units_to_cents(amount.to_units()),
                        },
                    )
                )
            elif spent_u + reserved_u + amount.to_units() > total_u:
                ok = False
                post_events.append(
                    (
                        EventKind.BUDGET_BLOCKED,
                        {
                            "scope": "company",
                            "used_cents": _units_to_cents(spent_u),
                            "limit_cents": _units_to_cents(total_u),
                            "attempted_cents": _units_to_cents(amount.to_units()),
                        },
                    )
                )
                conn.execute(
                    "UPDATE budget_state SET blocked = 1, last_warn_pct = ? "
                    "WHERE id = 1",
                    (float(BLOCKED_PCT),),
                )
            else:
                ok = True
                conn.execute(
                    "INSERT INTO expenses "
                    "(id, agent_id, amount_units, category, memo, approval_id, "
                    " ts_company, ts_real) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        expense_id,
                        agent_id,
                        amount.to_units(),
                        category.value,
                        memo,
                        approval_id,
                        ts_company.isoformat(),
                        ts_real.isoformat(),
                    ),
                )
                post_events.extend(self._evaluate_thresholds_locked(conn))

        if ok:
            self._events.append(
                EventKind.EXPENSE_CHARGED,
                {
                    "amount_cents": _units_to_cents(amount.to_units()),
                    "category": category.value,
                    "note": memo,
                    "request_id": expense_id,
                },
                actor=agent_id,
            )
        for kind, payload in post_events:
            self._events.append(kind, payload, actor=agent_id)

        if ok:
            return ChargeResult(ok=True, expense_id=expense_id)
        return ChargeResult(ok=False, reason="over_budget")

    # ------------------------------------------------------------ reserve

    def reserve(self, amount: Money, ref: str) -> Reservation:
        if amount.to_units() < 0:
            raise ValueError("reservation amount must be non-negative")

        post_events: list[tuple[EventKind, dict[str, object]]] = []
        rid = _new_id()
        created_at = _utcnow()

        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT total_units, blocked FROM budget_state WHERE id = 1"
            ).fetchone()
            total_u = int(row["total_units"])
            blocked = bool(row["blocked"])
            spent_u = self._sum_expenses(conn)
            reserved_u = self._sum_open_reservations(conn)

            if blocked or spent_u + reserved_u + amount.to_units() > total_u:
                raise OverBudget(
                    f"reserve refused: spent={spent_u} reserved={reserved_u} "
                    f"requested={amount.to_units()} total={total_u} "
                    f"blocked={blocked}"
                )

            conn.execute(
                "INSERT INTO reservations "
                "(id, amount_units, ref, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    rid,
                    amount.to_units(),
                    ref,
                    ReservationStatus.OPEN.value,
                    created_at.isoformat(),
                ),
            )
            post_events.extend(self._evaluate_thresholds_locked(conn))

        for kind, payload in post_events:
            self._events.append(kind, payload)

        return Reservation(
            id=rid,
            amount=amount,
            ref=ref,
            status=ReservationStatus.OPEN,
            created_at=created_at,
        )

    def release(self, reservation_id: str) -> None:
        with self._db.transaction() as conn:
            self._set_reservation_status_locked(
                conn, reservation_id, ReservationStatus.RELEASED
            )

    def commit(self, reservation_id: str, actual_amount: Money) -> ChargeResult:
        """Close a reservation and record the actual expense.

        ``actual_amount`` may exceed the reserved amount; the extra is
        evaluated against headroom (``total - spent - other reserved``). If
        even the actual exceeds the budget the reservation is released, the
        block flag is set, and a ``BUDGET_BLOCKED`` event fires.
        """
        if actual_amount.to_units() < 0:
            raise ValueError("actual amount must be non-negative")

        post_events: list[tuple[EventKind, dict[str, object]]] = []
        expense_id = _new_id()
        ts = _utcnow()
        ok: bool
        ref_for_event: str

        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT amount_units, ref, status FROM reservations WHERE id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise UnknownReservation(reservation_id)
            if row["status"] != ReservationStatus.OPEN.value:
                raise ReservationAlreadyClosed(reservation_id)
            ref_for_event = row["ref"]

            state_row = conn.execute(
                "SELECT total_units, blocked FROM budget_state WHERE id = 1"
            ).fetchone()
            total_u = int(state_row["total_units"])
            blocked = bool(state_row["blocked"])
            spent_u = self._sum_expenses(conn)
            other_reserved_u = self._sum_open_reservations(conn) - int(
                row["amount_units"]
            )

            committed_u = actual_amount.to_units()

            if blocked or spent_u + other_reserved_u + committed_u > total_u:
                # release the reservation and refuse the commit
                conn.execute(
                    "UPDATE reservations SET status = ? WHERE id = ?",
                    (ReservationStatus.RELEASED.value, reservation_id),
                )
                conn.execute(
                    "UPDATE budget_state SET blocked = 1, last_warn_pct = ? "
                    "WHERE id = 1",
                    (float(BLOCKED_PCT),),
                )
                post_events.append(
                    (
                        EventKind.BUDGET_BLOCKED,
                        {
                            "scope": "company",
                            "used_cents": _units_to_cents(spent_u),
                            "limit_cents": _units_to_cents(total_u),
                            "attempted_cents": _units_to_cents(committed_u),
                        },
                    )
                )
                ok = False
            else:
                conn.execute(
                    "UPDATE reservations SET status = ? WHERE id = ?",
                    (ReservationStatus.COMMITTED.value, reservation_id),
                )
                conn.execute(
                    "INSERT INTO expenses "
                    "(id, agent_id, amount_units, category, memo, approval_id, "
                    " ts_company, ts_real) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        expense_id,
                        None,
                        committed_u,
                        Category.OTHER.value,
                        f"commit:{ref_for_event}",
                        None,
                        ts.isoformat(),
                        ts.isoformat(),
                    ),
                )
                post_events.extend(self._evaluate_thresholds_locked(conn))
                ok = True

        if ok:
            self._events.append(
                EventKind.EXPENSE_CHARGED,
                {
                    "amount_cents": _units_to_cents(actual_amount.to_units()),
                    "category": Category.OTHER.value,
                    "note": f"commit:{ref_for_event}",
                    "request_id": expense_id,
                },
            )
        for kind, payload in post_events:
            self._events.append(kind, payload)

        return (
            ChargeResult(ok=True, expense_id=expense_id)
            if ok
            else ChargeResult(ok=False, reason="over_budget")
        )

    def reservations(self) -> list[Reservation]:
        rows = self._db.connect().execute(
            "SELECT id, amount_units, ref, status, created_at "
            "FROM reservations WHERE status = ?",
            (ReservationStatus.OPEN.value,),
        ).fetchall()
        return [
            Reservation(
                id=r["id"],
                amount=Money.from_units(int(r["amount_units"])),
                ref=r["ref"],
                status=ReservationStatus(r["status"]),
                created_at=_parse_dt(r["created_at"]),
            )
            for r in rows
        ]

    # ----------------------------------------------------------- internals

    def _sum_expenses(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_units), 0) AS s FROM expenses"
        ).fetchone()
        return int(row["s"])

    def _sum_open_reservations(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_units), 0) AS s "
            "FROM reservations WHERE status = ?",
            (ReservationStatus.OPEN.value,),
        ).fetchone()
        return int(row["s"])

    def _set_reservation_status_locked(
        self,
        conn: sqlite3.Connection,
        rid: str,
        new_status: ReservationStatus,
    ) -> None:
        row = conn.execute(
            "SELECT status FROM reservations WHERE id = ?", (rid,)
        ).fetchone()
        if row is None:
            raise UnknownReservation(rid)
        if row["status"] != ReservationStatus.OPEN.value:
            raise ReservationAlreadyClosed(rid)
        conn.execute(
            "UPDATE reservations SET status = ? WHERE id = ?",
            (new_status.value, rid),
        )

    def _evaluate_thresholds_locked(
        self, conn: sqlite3.Connection
    ) -> list[tuple[EventKind, dict[str, object]]]:
        """Compare current usage to thresholds; emit (after commit) and
        update ``last_warn_pct`` / ``blocked``."""
        row = conn.execute(
            "SELECT total_units, last_warn_pct, blocked "
            "FROM budget_state WHERE id = 1"
        ).fetchone()
        total_u = int(row["total_units"])
        last_pct = Decimal(str(row["last_warn_pct"]))
        blocked = bool(row["blocked"])

        spent_u = self._sum_expenses(conn)
        reserved_u = self._sum_open_reservations(conn)
        used_u = spent_u + reserved_u

        new_pct = (
            Decimal(0)
            if total_u <= 0
            else (Decimal(used_u) / Decimal(total_u)) * Decimal(100)
        )

        out: list[tuple[EventKind, dict[str, object]]] = []
        new_last = last_pct
        new_blocked = blocked

        # crossings upward
        if new_pct >= BLOCKED_PCT and last_pct < BLOCKED_PCT:
            out.append(
                (
                    EventKind.BUDGET_BLOCKED,
                    {
                        "scope": "company",
                        "used_cents": _units_to_cents(used_u),
                        "limit_cents": _units_to_cents(total_u),
                        "attempted_cents": 0,
                    },
                )
            )
            new_last = BLOCKED_PCT
            new_blocked = True
        elif new_pct >= WARNING_PCT and last_pct < WARNING_PCT:
            out.append(
                (
                    EventKind.BUDGET_WARNING,
                    {
                        "scope": "company",
                        "used_cents": _units_to_cents(used_u),
                        "limit_cents": _units_to_cents(total_u),
                        "ratio": float(new_pct / Decimal(100)),
                    },
                )
            )
            new_last = WARNING_PCT

        # crossings downward (e.g. after set_total raised the cap)
        if new_pct < WARNING_PCT and last_pct >= WARNING_PCT:
            new_last = Decimal(0)
        elif new_pct < BLOCKED_PCT and last_pct >= BLOCKED_PCT:
            new_last = WARNING_PCT
        if new_pct < BLOCKED_PCT and blocked:
            new_blocked = False

        if new_last != last_pct or new_blocked != blocked:
            conn.execute(
                "UPDATE budget_state SET last_warn_pct = ?, blocked = ? "
                "WHERE id = 1",
                (float(new_last), 1 if new_blocked else 0),
            )

        return out


def _units_to_cents(units: int) -> int:
    """Convert internal 1e-4 USD units to cents (1e-2 USD), rounding half up."""
    return (units + 50) // 100
