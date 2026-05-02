"""External collaborators required by :class:`modules.identity.Org`.

The Approvals module is built later; for now we depend only on a small
:class:`Protocol` so tests can supply a fake.
"""
from __future__ import annotations

from typing import Protocol


class ApprovalRequester(Protocol):
    """Channel into the Approvals module.

    The implementation must persist the request and, when the decider
    eventually decides, call :meth:`modules.identity.Org.complete_fire` with
    the same ``request_id``.
    """

    def request_fire(
        self,
        *,
        request_id: str,
        actor_id: str,
        target_id: str,
        decider_id: str,
        reason: str,
    ) -> None: ...
