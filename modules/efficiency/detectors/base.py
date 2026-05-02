"""Detector contract + helpers.

Detectors are pure functions (or stateless callables): they take a
:class:`DetectorContext` snapshot and return zero or more
:class:`FindingCandidate` objects describing currently-firing subjects.

The :class:`EfficiencyService` then diffs the candidate list against the
currently-open findings of the same ``detector_code`` to open new ones,
refresh existing ones, or auto-close stale ones.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from modules.event_store import Event

from ..config import EfficiencyConfig
from ..models import FindingCandidate

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from modules.approvals import Approvals
    from modules.identity import Org
    from modules.storage import CompanyDB


@dataclass(frozen=True)
class DetectorContext:
    """Read-only snapshot handed to every detector at each tick."""

    now: datetime
    events: list[Event]
    config: EfficiencyConfig
    org: Org | None = None
    approvals: Approvals | None = None
    db: CompanyDB | None = None


Detector = Callable[[DetectorContext], list[FindingCandidate]]
"""All detectors share this signature."""
