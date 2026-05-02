"""Tiny adapters between modules whose protocols don't line up perfectly.

Two cases live here:

* :class:`EventStoreEventSink` — adapts :class:`EventStore.append` to the
  ``emit(kind, payload)`` shape Storage's :class:`EventSink` protocol
  expects.
* :class:`FireApprovalAdapter` — bridges Identity's ``request_fire``
  channel to the real :class:`Approvals` service so the factory can wire
  Org without a fake.
"""
from __future__ import annotations

from typing import Any, ClassVar

from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    Approvals,
    RouteTarget,
)
from modules.event_store import EventKind, EventStore
from modules.identity import Org, Status


class EventStoreEventSink:
    """Adapter so :class:`Workspace` can call ``emit(...)`` while events
    are persisted via :meth:`EventStore.append`.

    Only a handful of kinds are forwarded — quota signals — and unknown
    kinds are silently ignored to avoid breaking workspace writes when the
    event store doesn't have a payload schema for the kind yet.
    """

    _SUPPORTED: ClassVar[dict[str, EventKind]] = {
        "quota.warning": EventKind.QUOTA_WARNING,
        "quota.exceeded": EventKind.QUOTA_EXCEEDED,
        "quota.drift": EventKind.QUOTA_DRIFT,
    }

    def __init__(self, events: EventStore) -> None:
        self._events = events

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        target = self._SUPPORTED.get(kind)
        if target is None:
            return
        actor = payload.get("agent_id")
        self._events.append(target, payload, actor=actor)


class FireApprovalAdapter:
    """Implements Identity's :class:`ApprovalRequester` on top of Approvals.

    Fire requests created by Org are persisted as ``FIRE_DEPTH_1`` approvals
    with a dedicated agent decider — exactly the route encoded by Identity.
    """

    def __init__(self, approvals: Approvals) -> None:
        self._approvals = approvals

    def request_fire(
        self,
        *,
        request_id: str,
        actor_id: str,
        target_id: str,
        decider_id: str,
        reason: str,
    ) -> None:
        self._approvals.request(
            request_id=request_id,
            kind=ApprovalKind.FIRE_DEPTH_1,
            requester_id=actor_id,
            payload={
                "actor_id": actor_id,
                "target_id": target_id,
                "reason": reason,
            },
            route=ApprovalRoute(
                target=RouteTarget.AGENT, agent_id=decider_id,
            ),
        )


__all__ = [
    "EventStoreEventSink",
    "FireApprovalAdapter",
    "MemoryIdentityAdapter",
]


class MemoryIdentityAdapter:
    """Implements Memory's :class:`IdentityProvider` Protocol on top of Org.

    Memory needs ``is_active`` (which Org doesn't expose directly) and
    ``can_read_workspace`` (which Org does). This thin wrapper bridges
    the two without modifying Org's public surface.
    """

    def __init__(self, org: Org) -> None:
        self._org = org

    def is_active(self, agent_id: str) -> bool:
        try:
            return self._org.get(agent_id).status is Status.ACTIVE
        except KeyError:
            return False

    def can_read_workspace(self, reader_id: str, owner_id: str) -> bool:
        return self._org.can_read_workspace(reader_id, owner_id)
