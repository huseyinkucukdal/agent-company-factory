"""Server-Sent Events: live tail of one company or every visible company.

The handler manages its own ``asyncio.Queue`` and a sync subscriber on
:class:`EventStore` rather than driving its async ``stream()`` generator.
The previous implementation wrapped ``stream_iter.__anext__()`` in
``asyncio.wait_for`` to interleave keepalives with events; on every
keepalive timeout the underlying generator was cancelled mid-``await``,
which left it in a corrupted state and silently stopped delivering live
events to the client (replay still worked because it bypasses streaming).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from modules.event_store import Event
from modules.factory import CompanyNotFound

from ..auth.deps import (
    AuthenticatedUser,
    require_user,
)
from ..deps import get_runtime
from ..rbac import UserRole
from ..runtime import BoardRuntime
from ._helpers import ensure_company_access, not_found

_log = logging.getLogger(__name__)

_QUEUE_BUFFER = 1024


def _format_sse(event: Event) -> bytes:
    """Render an event in the ``data: <json>\\n\\n`` SSE wire format.

    We deliberately omit the ``event: <kind>`` line. With a named SSE
    event type the browser routes the frame to a kind-specific
    ``addEventListener`` callback instead of the default ``onmessage``;
    our UI only listens on ``onmessage``, so emitting named events made
    every frame invisible while the connection still looked healthy.
    Keep ``id:`` so reconnects can resume via ``Last-Event-ID``.
    """
    body = {
        "id": event.id,
        "company_id": event.company_id,
        "kind": event.kind.value,
        "payload": event.payload,
        "actor_agent_id": event.actor_agent_id,
        "correlation_id": event.correlation_id,
        "ts_company": event.ts_company.isoformat(),
        "ts_real": event.ts_real.isoformat(),
    }
    return (
        f"id: {event.id}\n"
        f"data: {json.dumps(body, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


def _format_keepalive() -> bytes:
    return b": keepalive\n\n"


def build_stream_router() -> APIRouter:
    router = APIRouter(tags=["stream"])

    @router.get("/companies/{company_id}/stream")
    async def stream_company(
        company_id: str,
        request: Request,
        since: int = Query(default=0, ge=0),
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> StreamingResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc

        heartbeat = float(
            getattr(runtime.auth.codec, "_settings").sse_heartbeat_seconds,
        )

        async def gen() -> AsyncIterator[bytes]:
            async for chunk in _company_stream(handle, since, heartbeat, request):
                yield chunk

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @router.get("/stream")
    async def stream_global(
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> StreamingResponse:
        if current.role is not UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "admin_required", "code": "forbidden"},
            )

        heartbeat = float(
            getattr(runtime.auth.codec, "_settings").sse_heartbeat_seconds,
        )
        company_ids = [s.company_id for s in runtime.factory.list_active()]
        handles = []
        for cid in company_ids:
            try:
                handles.append(runtime.factory.get_handle(cid))
            except CompanyNotFound:
                continue

        async def gen() -> AsyncIterator[bytes]:
            async for chunk in _multi_stream(handles, heartbeat, request):
                yield chunk

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return router


# --------------------------------------------------------------------- internals


async def _company_stream(
    handle: Any, since: int, heartbeat: float, request: Request,
) -> AsyncIterator[bytes]:
    """Yield SSE bytes for a single company's event log.

    Subscribe first, then flush the backlog, then drain the local queue.
    Subscribing first means events that fire *during* the backlog read
    still reach us; ``last_id`` deduplicates so we never emit twice.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_BUFFER)
    last_id = since

    def _sub(event: Event) -> None:
        # ``EventStore`` fires subscribers synchronously after commit
        # from whichever thread/coroutine invoked ``append``. Using
        # ``call_soon_threadsafe`` is the only safe way to push into an
        # asyncio.Queue from a callback we don't fully own — without it
        # ``put_nowait`` from a wrong thread silently corrupts the loop's
        # internal state, which is why the live feed appeared to "stop".
        loop.call_soon_threadsafe(_safe_put, queue, event)

    sub_handle = handle.events.subscribe(_sub)
    try:
        # Prime the wire so the client sees ``onopen`` immediately even
        # when the company has zero backlog and no live traffic.
        yield b": ready\n\n"

        for evt in handle.events.read(since=since, limit=10_000):
            yield _format_sse(evt)
            last_id = max(last_id, evt.id)

        while True:
            if await request.is_disconnected():
                return
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield _format_keepalive()
                continue
            if evt.id <= last_id:
                continue
            last_id = evt.id
            yield _format_sse(evt)
    finally:
        with contextlib.suppress(Exception):
            handle.events.unsubscribe(sub_handle)


async def _multi_stream(
    handles: list[Any], heartbeat: float, request: Request,
) -> AsyncIterator[bytes]:
    """Same idea as :func:`_company_stream` but fans in N companies."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_BUFFER * 4)
    last_seen: dict[str, int] = {h.company_id: 0 for h in handles}

    def _make_sub(_company_id: str):
        def _push(event: Event) -> None:
            loop.call_soon_threadsafe(_safe_put, queue, event)
        return _push

    sub_handles: list[tuple[Any, Any]] = []
    try:
        for h in handles:
            sub_handles.append((h, h.events.subscribe(_make_sub(h.company_id))))

        # Send a tiny stream comment so EventSource fires `onopen` quickly.
        yield b": ready\n\n"

        while True:
            if await request.is_disconnected():
                return
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield _format_keepalive()
                continue
            cid = evt.company_id
            if evt.id <= last_seen.get(cid, 0):
                continue
            last_seen[cid] = evt.id
            yield _format_sse(evt)
    finally:
        for h, sub in sub_handles:
            with contextlib.suppress(Exception):
                h.events.unsubscribe(sub)


def _safe_put(queue: asyncio.Queue[Event], event: Event) -> None:
    """Non-blocking put with overflow-drop semantics.

    Runs on the event loop thread (via ``call_soon_threadsafe``). If the
    queue is full we drop the *oldest* item to keep the SSE feed live —
    losing one stale notification is preferable to deadlocking the
    producer (the EventStore subscribe callback is fire-and-forget).
    """
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(event)


__all__ = ["build_stream_router"]
