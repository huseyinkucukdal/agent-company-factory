"""External collaborators required by :class:`modules.memory.Memory`."""
from __future__ import annotations

from typing import Protocol


class IdentityProvider(Protocol):
    """Subset of :class:`modules.identity.Org` consumed by Memory.

    Memory only needs to ask two questions:

    * Is ``agent_id`` an active (not-fired) agent?  Recall excludes fired
      owners' memories from cross-agent search even though the rows persist
      for replay.
    * May ``reader`` access ``owner``'s memory?  Same rule as workspace ACL:
      self or direct manager.
    """

    def is_active(self, agent_id: str) -> bool: ...

    def can_read_workspace(
        self, reader_id: str, owner_id: str
    ) -> bool: ...
