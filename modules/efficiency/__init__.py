"""Module 18 — Efficiency & Diagnostics.

Passive consumer that detects inefficiencies (loops, stalls, cost-without-output,
approval backlogs, etc.) and surfaces them as :class:`Finding` records.
Never takes corrective action; only reports.
"""
from __future__ import annotations

from .config import EfficiencyConfig, default_config
from .exceptions import EfficiencyError, FindingNotFound, InvalidTransition
from .models import (
    Finding,
    FindingCandidate,
    FindingStatus,
    Severity,
    SubjectType,
)
from .service import EfficiencyService
from .store import FindingStore

__all__ = [
    "EfficiencyConfig",
    "EfficiencyError",
    "EfficiencyService",
    "Finding",
    "FindingCandidate",
    "FindingNotFound",
    "FindingStatus",
    "FindingStore",
    "InvalidTransition",
    "Severity",
    "SubjectType",
    "default_config",
]
