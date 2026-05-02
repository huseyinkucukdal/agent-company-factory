"""``Clock``: company-time vs real-time, pause/resume, day-tick emitter.

Design choices
--------------
* All wall-clock reads go through an injectable ``wall_now`` callable so
  tests can pin or fast-forward time without ``asyncio.sleep``.
* Backwards wall-clock jumps (NTP corrections, manual time changes) cannot
  rewind company time: ``now_company`` clamps to the previously returned
  value via ``_last_company_time``.
* The state machine is driven by ``pump_once``. ``start`` simply schedules
  ``pump_once`` periodically. Tests skip ``start`` and call ``pump_once``
  directly for determinism.
* Persistence: a singleton row in ``clock_state``. ``_load_or_init`` on
  construction restores state across process restarts.
* Pause-during-crash policy (per PLAN's open question, default chosen):
  the wall-time gap between crash and restart counts as paused — i.e. on
  restart we extend ``paused_total_sec`` by the elapsed wall time since
  ``pause_started_at`` and reset the pause anchor.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from modules.event_store import EventKind, EventStore
from modules.storage import CompanyDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .protocols import OrchestratorIdleProbe
from .state import ClockRate, ClockState

_log = logging.getLogger(__name__)
_MODULE_KEY = "clock"
_DEFAULT_PUMP_INTERVAL = 1.0  # seconds


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Clock:
    """Per-company clock."""

    def __init__(
        self,
        db: CompanyDB,
        events: EventStore,
        idle_probe: OrchestratorIdleProbe,
        rate: ClockRate | None = None,
        *,
        wall_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._events = events
        self._idle_probe = idle_probe
        self._rate = rate or ClockRate.realtime()
        self._wall_now = wall_now or _utcnow

        self._state: ClockState = ClockState.RUNNING
        self._epoch_real: datetime = self._wall_now()
        self._epoch_company: datetime = self._epoch_real
        self._paused_total_sec: float = 0.0
        self._pause_started_at: datetime | None = None
        self._last_day_ticked: int = 0
        self._last_company_time: datetime | None = None

        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    # --------------------------------------------------------------- migrate

    def migrate(self) -> None:
        """Apply schema migrations and load (or initialise) the singleton row."""
        migrations = load_migrations_from_package("modules.clock.migrations")
        apply_migrations(self._db.connect(), module=_MODULE_KEY, migrations=migrations)
        self._load_or_init()

    # ------------------------------------------------------------ persistence

    def _load_or_init(self) -> None:
        conn = self._db.connect()
        row = conn.execute("SELECT * FROM clock_state WHERE id = 1").fetchone()
        if row is None:
            now = self._wall_now()
            self._state = ClockState.RUNNING
            self._epoch_real = now
            self._epoch_company = now
            self._paused_total_sec = 0.0
            self._pause_started_at = None
            self._last_day_ticked = 0
            with self._db.transaction() as tconn:
                tconn.execute(
                    "INSERT INTO clock_state "
                    "(id, state, epoch_real, epoch_company, paused_total_sec, "
                    " pause_started_at, last_day_ticked, rate_seconds_per_day) "
                    "VALUES (1, ?, ?, ?, 0, NULL, 0, ?)",
                    (
                        self._state.value,
                        now.isoformat(),
                        now.isoformat(),
                        self._rate.real_seconds_per_company_day,
                    ),
                )
            return

        self._state = ClockState(row["state"])
        self._epoch_real = datetime.fromisoformat(row["epoch_real"])
        self._epoch_company = datetime.fromisoformat(row["epoch_company"])
        self._paused_total_sec = float(row["paused_total_sec"])
        self._pause_started_at = _parse(row["pause_started_at"])
        self._last_day_ticked = int(row["last_day_ticked"])

        # If we crashed while paused, the gap counts as paused time.
        if (
            self._state in (ClockState.PAUSING, ClockState.PAUSED)
            and self._pause_started_at is not None
        ):
            now = self._wall_now()
            extra = max(0.0, (now - self._pause_started_at).total_seconds())
            self._paused_total_sec += extra
            self._pause_started_at = now
            self._persist()

    def _persist(self) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE clock_state SET state = ?, epoch_real = ?, epoch_company = ?, "
                "paused_total_sec = ?, pause_started_at = ?, last_day_ticked = ?, "
                "rate_seconds_per_day = ? WHERE id = 1",
                (
                    self._state.value,
                    self._epoch_real.isoformat(),
                    self._epoch_company.isoformat(),
                    self._paused_total_sec,
                    self._pause_started_at.isoformat()
                    if self._pause_started_at
                    else None,
                    self._last_day_ticked,
                    self._rate.real_seconds_per_company_day,
                ),
            )

    # --------------------------------------------------------------- queries

    def state(self) -> ClockState:
        return self._state

    def now_real(self) -> datetime:
        return self._wall_now()

    def now_company(self) -> datetime:
        """Compute the current company-time, never going backwards."""
        if self._state in (ClockState.PAUSED, ClockState.PAUSING):
            # Paused time is frozen. Pausing also freezes the company clock —
            # the queue is closed and the world should not appear to advance.
            value = self._compute_company_time(freeze_at=self._pause_started_at)
        else:
            value = self._compute_company_time()

        if self._last_company_time is not None and value < self._last_company_time:
            value = self._last_company_time
        self._last_company_time = value
        return value

    def _compute_company_time(self, *, freeze_at: datetime | None = None) -> datetime:
        anchor = freeze_at if freeze_at is not None else self._wall_now()
        elapsed_real = (anchor - self._epoch_real).total_seconds() - self._paused_total_sec
        if elapsed_real < 0:
            elapsed_real = 0.0
        company_seconds = elapsed_real * self._rate.factor
        return self._epoch_company + timedelta(seconds=company_seconds)

    def is_accepting_messages(self) -> bool:
        return self._state is ClockState.RUNNING

    # ----------------------------------------------------------- transitions

    async def pause(self, requested_by: str | None = None) -> None:
        if self._state in (ClockState.PAUSING, ClockState.PAUSED):
            return
        self._state = ClockState.PAUSING
        self._pause_started_at = self._wall_now()
        self._persist()
        self._events.append(
            EventKind.CLOCK_PAUSED,
            {"by": requested_by},
        )
        # Try to settle immediately if everyone is already idle.
        await self._maybe_finalise_pause()

    async def resume(self, requested_by: str | None = None) -> None:
        if self._state is ClockState.RUNNING:
            return
        # Account for the time spent paused (or pausing).
        if self._pause_started_at is not None:
            elapsed = (self._wall_now() - self._pause_started_at).total_seconds()
            self._paused_total_sec += max(0.0, elapsed)
            self._pause_started_at = None
        self._state = ClockState.RUNNING
        self._persist()
        self._events.append(
            EventKind.CLOCK_RESUMED,
            {"by": requested_by},
        )

    async def _maybe_finalise_pause(self) -> None:
        if self._state is not ClockState.PAUSING:
            return
        try:
            idle = self._idle_probe.is_company_idle()
        except Exception:
            _log.exception("idle_probe raised; staying in pausing state")
            return
        if idle:
            self._state = ClockState.PAUSED
            self._persist()

    # ------------------------------------------------------------------ pump

    async def pump_once(self) -> None:
        """One iteration of the background loop.

        * Emits any pending DAY_TICK events.
        * Promotes ``pausing → paused`` if all agents are idle.
        """
        await self._emit_day_ticks()
        await self._maybe_finalise_pause()

    async def _emit_day_ticks(self) -> None:
        company_now = self.now_company()
        elapsed_company_seconds = (company_now - self._epoch_company).total_seconds()
        current_day = int(elapsed_company_seconds // 86400)
        if current_day <= self._last_day_ticked:
            return
        for day in range(self._last_day_ticked + 1, current_day + 1):
            self._events.append(EventKind.DAY_TICK, {"day": day})
        self._last_day_ticked = current_day
        self._persist()

    # ----------------------------------------------------------------- start

    async def start(self, *, interval: float = _DEFAULT_PUMP_INTERVAL) -> None:
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(interval))

    async def stop(self) -> None:
        if self._task is None or self._stop_event is None:
            return
        self._stop_event.set()
        try:
            await self._task
        finally:
            self._task = None
            self._stop_event = None

    async def _run(self, interval: float) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.pump_once()
            except Exception:
                _log.exception("clock pump_once failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except TimeoutError:
                continue
