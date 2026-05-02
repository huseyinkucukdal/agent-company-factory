"""The :class:`EfficiencyService` — passive detector loop.

Periodically pulls a bounded slice of recent events from the
:class:`EventStore`, runs every registered detector, and reconciles the
candidate list with :class:`FindingStore`. Emits
``efficiency.finding.opened`` and ``efficiency.finding.closed`` events
so the Board UI can react in real time.

:meth:`tick` is callable directly so unit tests can step the service
without the async machinery.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from modules.event_store import EventKind, EventStore

from .config import EfficiencyConfig, default_config
from .detectors import R1_DETECTORS, DetectorContext
from .models import FindingCandidate, FindingStatus, SubjectType
from .store import FindingStore

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from modules.approvals import Approvals
    from modules.identity import Org
    from modules.storage import CompanyDB

_log = logging.getLogger(__name__)

# Bound the per-tick scan. Must comfortably cover the largest detector window.
_READ_LIMIT = 5000


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EfficiencyService:
    """Per-company detector loop. Constructed once at company bootstrap."""

    def __init__(
        self,
        *,
        company_id: str,
        db: CompanyDB,
        events: EventStore,
        store: FindingStore,
        approvals: Approvals | None = None,
        org: Org | None = None,
        config: EfficiencyConfig | None = None,
        time_provider: Callable[[], datetime] | None = None,
        tick_interval_seconds: float = 5.0,
    ) -> None:
        self._company_id = company_id
        self._db = db
        self._events = events
        self._store = store
        self._approvals = approvals
        self._org = org
        self._config = config or default_config()
        self._now = time_provider or _utcnow
        self._tick_interval = tick_interval_seconds

        self._loop_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        # Detectors whose required tables are missing get parked here so we
        # don't spam logs every tick. They get retried after a schema repair
        # attempt clears the set.
        self._missing_table_detectors: set[str] = set()

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        self._stopped.clear()
        self._loop_task = asyncio.create_task(
            self._run_loop(), name=f"efficiency-loop[{self._company_id}]"
        )

    async def stop(self) -> None:
        self._stopped.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._loop_task
            self._loop_task = None

    # --------------------------------------------------------------- runtime

    async def _run_loop(self) -> None:
        missing_table_logged = False
        while not self._stopped.is_set():
            try:
                self.tick()
                missing_table_logged = False
            except sqlite3.OperationalError as exc:
                # If the schema_version row got out of sync with the actual
                # tables (e.g. a partially-restored DB), reapply migrations
                # once and keep going. Log the spam-ish error only once.
                if "no such table" in str(exc).lower():
                    try:
                        self._store.migrate()
                        # Schema repaired — let parked detectors retry.
                        self._missing_table_detectors.clear()
                    except Exception:
                        if not missing_table_logged:
                            _log.exception(
                                "efficiency: schema repair failed; pausing detector"
                            )
                            missing_table_logged = True
                else:
                    _log.exception("efficiency: detector tick raised")
            except Exception:
                _log.exception("efficiency: detector tick raised")
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self._tick_interval
                )
            except TimeoutError:
                continue

    # -------------------------------------------------------- public stepper

    def tick(self) -> list[FindingCandidate]:
        """Run every detector once. Returns this tick's candidates (for tests)."""
        now = self._now()
        recent = self._events.read(limit=_READ_LIMIT)
        cutoff = now.timestamp() - self._config.max_window_seconds
        windowed = [e for e in recent if e.ts_company.timestamp() >= cutoff]

        ctx = DetectorContext(
            now=now,
            events=windowed,
            config=self._config,
            org=self._org,
            approvals=self._approvals,
            db=self._db,
        )
        all_candidates: list[FindingCandidate] = []
        for code, detector in R1_DETECTORS:
            if code in self._missing_table_detectors:
                continue
            try:
                candidates = detector(ctx)
                self._reconcile(code, candidates)
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    _log.warning(
                        "efficiency detector %s disabled: %s "
                        "(will retry after schema repair)",
                        code, exc,
                    )
                    self._missing_table_detectors.add(code)
                else:
                    _log.exception("efficiency detector %s raised", code)
                continue
            except Exception:
                _log.exception("efficiency detector %s raised", code)
                continue
            all_candidates.extend(candidates)
        return all_candidates

    # ------------------------------------------------------- reconciliation

    def _reconcile(
        self, detector_code: str, candidates: list[FindingCandidate]
    ) -> None:
        firing_keys: set[tuple[str, SubjectType, str | None]] = set()
        for cand in candidates:
            if cand.detector_code != detector_code:
                continue
            firing_keys.add(
                (cand.detector_code, cand.subject_type, cand.subject_id)
            )
            finding, opened = self._store.upsert_from_candidate(cand)
            if opened:
                self._safe_emit_opened(finding.id, cand)

        for existing in self._store.list_open(detector_code=detector_code):
            key = (
                existing.detector_code,
                existing.subject_type,
                existing.subject_id,
            )
            if key in firing_keys:
                continue
            closed = self._store.auto_close(existing.id)
            if closed.status is FindingStatus.CLOSED:
                self._safe_emit_closed(existing.id, detector_code, "resolved")

    def _safe_emit_opened(
        self, finding_id: str, candidate: FindingCandidate
    ) -> None:
        try:
            self._events.append(
                EventKind.EFFICIENCY_FINDING_OPENED,
                {
                    "finding_id": finding_id,
                    "detector_code": candidate.detector_code,
                    "severity": candidate.severity.value,
                    "subject_type": candidate.subject_type.value,
                    "subject_id": candidate.subject_id,
                    "evidence": candidate.evidence,
                    "recommendation": candidate.recommendation,
                },
            )
        except Exception:
            _log.exception("efficiency: emit finding.opened failed")

    def _safe_emit_closed(
        self, finding_id: str, detector_code: str, reason: str
    ) -> None:
        try:
            self._events.append(
                EventKind.EFFICIENCY_FINDING_CLOSED,
                {
                    "finding_id": finding_id,
                    "detector_code": detector_code,
                    "reason": reason,
                },
            )
        except Exception:
            _log.exception("efficiency: emit finding.closed failed")

    # -------------------------------------------------------- board API ops

    @property
    def store(self) -> FindingStore:
        return self._store

    def acknowledge(self, finding_id: str) -> None:
        self._store.acknowledge(finding_id)

    def manual_close(self, finding_id: str) -> None:
        finding = self._store.close(finding_id)
        if finding.status is FindingStatus.CLOSED:
            self._safe_emit_closed(finding.id, finding.detector_code, "manual")
