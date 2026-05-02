"""Specifications and summaries used by the Company Factory.

Everything here is plain data; no DB, no IO. Construction may raise
:class:`InvalidSpec` to keep the factory entry point clean.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from modules.clock import ClockRate
from modules.identity import Role

from .exceptions import InvalidSpec


class CompanyStatus(StrEnum):
    CREATING = "creating"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True)
class ExtraAgentSpec:
    """A non-bootstrap agent the factory should hire during creation."""

    role_title: str
    first_name: str
    last_name: str
    role_description: str = ""
    persona_ref: str = "default"
    reports_to_role: Role = Role.CEO

    def __post_init__(self) -> None:
        if not self.role_title.strip():
            raise InvalidSpec("role_title cannot be blank")
        if not self.first_name.strip():
            raise InvalidSpec("first_name cannot be blank")
        if not self.last_name.strip():
            raise InvalidSpec("last_name cannot be blank")


@dataclass(frozen=True)
class CompanySpec:
    """User-supplied recipe to create a company.

    All numeric inputs are validated up-front to fail fast in the API layer.
    `company_id` is optional — the factory generates one when omitted.
    """

    name: str
    mission: str
    initial_budget_usd: Decimal | float | int = 1000
    company_disk_quota_mb: int = 1024
    default_agent_quota_mb: int = 64
    industry: str | None = None
    extra_agents: tuple[ExtraAgentSpec, ...] = ()
    auto_approve_threshold_usd: Decimal | float | int = 0
    clock_rate: ClockRate = field(default_factory=ClockRate.realtime)
    company_id: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise InvalidSpec("name must be non-empty")
        if not self.mission or not self.mission.strip():
            raise InvalidSpec("mission must be non-empty")
        if Decimal(str(self.initial_budget_usd)) <= 0:
            raise InvalidSpec("initial_budget_usd must be positive")
        if self.company_disk_quota_mb <= 0:
            raise InvalidSpec("company_disk_quota_mb must be positive")
        if self.default_agent_quota_mb <= 0:
            raise InvalidSpec("default_agent_quota_mb must be positive")
        if Decimal(str(self.auto_approve_threshold_usd)) < 0:
            raise InvalidSpec("auto_approve_threshold_usd must be non-negative")

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mission": self.mission,
            "initial_budget_usd": str(Decimal(str(self.initial_budget_usd))),
            "company_disk_quota_mb": self.company_disk_quota_mb,
            "default_agent_quota_mb": self.default_agent_quota_mb,
            "industry": self.industry,
            "extra_agents": [
                {
                    "role_title": ex.role_title,
                    "first_name": ex.first_name,
                    "last_name": ex.last_name,
                    "role_description": ex.role_description,
                    "persona_ref": ex.persona_ref,
                    "reports_to_role": ex.reports_to_role.value,
                }
                for ex in self.extra_agents
            ],
            "auto_approve_threshold_usd": str(
                Decimal(str(self.auto_approve_threshold_usd))
            ),
            "clock_rate_seconds_per_day": (
                self.clock_rate.real_seconds_per_company_day
            ),
            "company_id": self.company_id,
        }


@dataclass(frozen=True)
class CompanySummary:
    """Read-only snapshot returned by :meth:`CompanyFactory.list_active`."""

    company_id: str
    name: str
    status: CompanyStatus
    created_at: datetime
    closed_at: datetime | None = None


__all__ = [
    "CompanySpec",
    "CompanyStatus",
    "CompanySummary",
    "ExtraAgentSpec",
]
