"""Domain types for the Tool Layer."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel

from modules.cost import Money
from modules.identity import Role


class ToolCategory(StrEnum):
    COMMUNICATION = "communication"
    WORKSPACE = "workspace"
    MEMORY = "memory"
    GOVERNANCE = "governance"
    EXTERNAL = "external"
    INTERNAL_DATA = "internal_data"


class ApprovalRequirement(StrEnum):
    NONE = "none"
    EXPENSE = "expense"
    EXTERNAL = "external_action"


RolesAllowed: TypeAlias = frozenset[Role] | Literal["any"]


CostEstimator: TypeAlias = Callable[[Any], Money]
PermissionCheck: TypeAlias = Callable[..., None]
ExecutorFn: TypeAlias = Callable[..., Any]


@dataclass(frozen=True)
class ToolContext:
    """Read-only execution context handed to permission checks and executors."""

    agent_id: str
    role: Role
    correlation_id: str
    services: ToolServices


@dataclass
class ToolServices:
    """Bundle of injected collaborators available to tool executors.

    Not frozen because the ``orchestrator`` reference is wired in *after*
    construction. There's a chicken-and-egg between ``Tools`` and
    ``Orchestrator`` at bootstrap (the orchestrator wants the tools, and
    the ``send_message`` tool needs the orchestrator), so the factory
    builds tools first and calls :meth:`Tools.set_orchestrator` once the
    orchestrator exists.
    """

    identity: Any
    workspace: Any
    memory: Any
    events: Any
    connector: Any
    cost: Any
    approvals: Any
    performance: Any = None
    orchestrator: Any = None
    hire_service: Any = None


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    schema: type[BaseModel]
    output_schema: type[BaseModel]
    category: ToolCategory
    roles_allowed: RolesAllowed
    requires_approval: ApprovalRequirement = ApprovalRequirement.NONE
    cost_estimator: CostEstimator | None = None
    permission_check: PermissionCheck | None = None
    executor: ExecutorFn | None = None

    def is_role_allowed(self, role: Role) -> bool:
        if self.roles_allowed == "any":
            return True
        return role in self.roles_allowed


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: BaseModel | None = None
    error_code: str | None = None
    error_message: str | None = None
    cost_usd: Decimal = field(default=Decimal("0.0000"))
    extra: dict[str, Any] = field(default_factory=dict)
