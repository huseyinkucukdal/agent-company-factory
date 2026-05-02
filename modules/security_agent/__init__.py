"""Module 12 — Security Agent.

Public surface: a deterministic rule engine plus a policy class that
plugs into Approvals, Event Store and Orchestrator.
"""
from __future__ import annotations

from .exceptions import PolicyViolation, SecurityError
from .models import ReviewOutcome, ReviewResult, SecurityFinding, Severity
from .policy import LLMReviewer, SecurityDeps, SecurityPolicy
from .static_rules import (
    RuleContext,
    StaticRuleSet,
    default_context,
)

__all__ = [
    "LLMReviewer",
    "PolicyViolation",
    "ReviewOutcome",
    "ReviewResult",
    "RuleContext",
    "SecurityDeps",
    "SecurityError",
    "SecurityFinding",
    "SecurityPolicy",
    "Severity",
    "StaticRuleSet",
    "default_context",
]
