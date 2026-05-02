"""Append-only event log with sync subscribers and async streaming.

Concurrency model
-----------------
* ``append`` performs a single ``BEGIN IMMEDIATE`` transaction on the
  per-company SQLite DB. The ``CompanyDB`` connection wrapper retries on
  transient lock contention.
* Synchronous subscribers (``subscribe``) are invoked **after** the
  transaction commits, so a callback that raises cannot abort the write.
  Exceptions are caught and logged; other subscribers still fire.
* Async streams are bounded ``asyncio.Queue`` instances, one per
  ``stream(...)`` call. ``append`` posts each new event to every queue from
  whatever thread it runs in via ``loop.call_soon_threadsafe``. When a queue
  is full the oldest event is dropped (a non-recursive drop counter is
  bumped — we deliberately do **not** emit a follow-up event to avoid an
  infinite loop).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules.storage import CompanyDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .exceptions import (
    PayloadTooLarge,
    PayloadValidationError,
    UnknownEventKind,
)
from .kinds import EventKind
from .payloads import model_for

_log = logging.getLogger(__name__)

_MODULE_KEY = "event_store"
_DEFAULT_MAX_PAYLOAD_BYTES = 256 * 1024
_DEFAULT_STREAM_BUFFER = 1000


@dataclass(frozen=True)
class Event:
    """One row from the ``events`` table."""

    id: int
    company_id: str
    kind: EventKind
    payload: dict[str, Any]
    actor_agent_id: str | None
    correlation_id: str | None
    ts_company: datetime
    ts_real: datetime


@dataclass(frozen=True)
class SubscriptionHandle:
    """Opaque token returned by :meth:`EventStore.subscribe`. Cancel via ``unsubscribe``."""

    id: str


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _coerce_kind(kind: EventKind | str) -> EventKind:
    if isinstance(kind, EventKind):
        return kind
    try:
        return EventKind(kind)
    except ValueError as exc:
        raise UnknownEventKind(str(kind)) from exc


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string emitted by ``datetime.isoformat``."""
    return datetime.fromisoformat(s)


class EventStore:
    """Per-company event store."""

    def __init__(
        self,
        db: CompanyDB,
        *,
        company_now: Callable[[], datetime] | None = None,
        max_payload_bytes: int = _DEFAULT_MAX_PAYLOAD_BYTES,
        stream_buffer_size: int = _DEFAULT_STREAM_BUFFER,
    ) -> None:
        self._db = db
        self._company_now = company_now or _utcnow
        self._max_payload_bytes = max_payload_bytes
        self._stream_buffer_size = stream_buffer_size

        self._sub_lock = threading.Lock()
        self._subscribers: dict[str, Callable[[Event], None]] = {}
        self._streams: dict[str, _StreamSubscription] = {}

    # ----------------------------------------------------------------- migrate

    def migrate(self) -> None:
        migrations = load_migrations_from_package("modules.event_store.migrations")
        apply_migrations(self._db.connect(), module=_MODULE_KEY, migrations=migrations)

    # ------------------------------------------------------------------ append

    def append(
        self,
        kind: EventKind | str,
        payload: dict[str, Any],
        *,
        actor: str | None = None,
        correlation: str | None = None,
    ) -> Event:
        """Validate, persist, and broadcast a new event."""
        kind_enum = _coerce_kind(kind)
        validated = self._validate_payload(kind_enum, payload)
        payload_json = json.dumps(validated, separators=(",", ":"), sort_keys=True)
        size = len(payload_json.encode("utf-8"))
        if size > self._max_payload_bytes:
            raise PayloadTooLarge(
                f"{kind_enum.value}: {size} bytes > {self._max_payload_bytes}"
            )

        ts_company = self._company_now()
        ts_real = _utcnow()

        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO events "
                "(kind, payload_json, actor_agent_id, correlation_id, "
                " ts_company, ts_real) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    kind_enum.value,
                    payload_json,
                    actor,
                    correlation,
                    ts_company.isoformat(),
                    ts_real.isoformat(),
                ),
            )
            event_id = int(cur.lastrowid or 0)

        event = Event(
            id=event_id,
            company_id=self._db.company_id,
            kind=kind_enum,
            payload=validated,
            actor_agent_id=actor,
            correlation_id=correlation,
            ts_company=ts_company,
            ts_real=ts_real,
        )

        self._broadcast(event)
        return event

    def _validate_payload(
        self, kind: EventKind, payload: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            model_cls = model_for(kind)
        except KeyError as exc:  # pragma: no cover — registry covers every kind
            raise UnknownEventKind(kind.value) from exc
        try:
            model = model_cls.model_validate(payload)
        except Exception as exc:  # pydantic.ValidationError is the typical case
            raise PayloadValidationError(
                f"{kind.value}: {exc}"
            ) from exc
        return model.model_dump(mode="json")

    # -------------------------------------------------------------------- read

    def read(
        self,
        *,
        since: int | None = None,
        until: int | None = None,
        kinds: list[EventKind] | None = None,
        actor: str | None = None,
        correlation: str | None = None,
        limit: int = 1000,
    ) -> list[Event]:
        """Filtered batch read, ordered by event id ascending."""
        if limit <= 0:
            raise ValueError("limit must be positive")

        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("id > ?")
            params.append(since)
        if until is not None:
            clauses.append("id <= ?")
            params.append(until)
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(k.value for k in kinds)
        if actor is not None:
            clauses.append("actor_agent_id = ?")
            params.append(actor)
        if correlation is not None:
            clauses.append("correlation_id = ?")
            params.append(correlation)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, kind, payload_json, actor_agent_id, correlation_id, "
            "       ts_company, ts_real "
            f"FROM events{where} ORDER BY id ASC LIMIT ?"
        )
        params.append(limit)

        try:
            rows = self._db.connect().execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            # Half-built companies (e.g. after a failed bootstrap that
            # the operator hasn't purged yet) may be missing this table.
            # Read should degrade to "no events" rather than spam every
            # caller with stack traces.
            if "no such table" in str(exc).lower():
                return []
            raise
        return [self._row_to_event(r) for r in rows]

    def _row_to_event(self, row: Any) -> Event:
        return Event(
            id=int(row["id"]),
            company_id=self._db.company_id,
            kind=EventKind(row["kind"]),
            payload=json.loads(row["payload_json"]),
            actor_agent_id=row["actor_agent_id"],
            correlation_id=row["correlation_id"],
            ts_company=_parse_iso(row["ts_company"]),
            ts_real=_parse_iso(row["ts_real"]),
        )

    # --------------------------------------------------------------- subscribe

    def subscribe(
        self, callback: Callable[[Event], None]
    ) -> SubscriptionHandle:
        """Register an in-process synchronous listener.

        Callback exceptions are swallowed (logged) so one bad subscriber
        cannot break the broadcast for the rest.
        """
        handle = SubscriptionHandle(id=uuid.uuid4().hex)
        with self._sub_lock:
            self._subscribers[handle.id] = callback
        return handle

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        with self._sub_lock:
            self._subscribers.pop(handle.id, None)

    # ----------------------------------------------------------------- stream

    async def stream(
        self,
        since: int = 0,
        *,
        buffer_size: int | None = None,
    ) -> AsyncGenerator[Event, None]:
        """Tail the log forever, starting from events with id > ``since``.

        Cancel by closing the generator (e.g. ``aclose()`` or breaking out
        of an ``async for`` loop).
        """
        loop = asyncio.get_running_loop()
        sub = _StreamSubscription(
            queue=asyncio.Queue(maxsize=buffer_size or self._stream_buffer_size),
            loop=loop,
        )
        sub_id = uuid.uuid4().hex
        try:
            backlog = self.read(since=since, limit=10_000)
            for evt in backlog:
                yield evt
            last_id = backlog[-1].id if backlog else since

            with self._sub_lock:
                self._streams[sub_id] = sub

            while True:
                evt = await sub.queue.get()
                if evt.id <= last_id:
                    # A backlog event leaked through during registration.
                    continue
                last_id = evt.id
                yield evt
        finally:
            with self._sub_lock:
                self._streams.pop(sub_id, None)

    # -------------------------------------------------------------- internals

    def _broadcast(self, event: Event) -> None:
        # Snapshot under lock to avoid mutation-during-iteration.
        with self._sub_lock:
            sync_subs = list(self._subscribers.values())
            streams = list(self._streams.values())

        for cb in sync_subs:
            try:
                cb(event)
            except Exception:
                _log.exception("event_store subscriber raised")

        for sub in streams:
            sub.deliver(event)


@dataclass
class _StreamSubscription:
    queue: asyncio.Queue[Event]
    loop: asyncio.AbstractEventLoop
    drops: int = 0

    def deliver(self, event: Event) -> None:
        """Push ``event`` onto the asyncio queue from any thread."""
        self.loop.call_soon_threadsafe(self._enqueue, event)

    def _enqueue(self, event: Event) -> None:
        if self.queue.full():
            try:
                _ = self.queue.get_nowait()
                self.drops += 1
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover — we just drained it
            self.drops += 1
