"""Module 08 — Tool Layer.

Public surface mirrors the PLAN: a :class:`Tools` registry that validates
arguments, gates by role/permission, reserves and commits cost, routes
EXPENSE/EXTERNAL operations through Approvals, and emits structured
``tool.called`` / ``tool.result`` / ``tool.denied`` events for replay.
"""
from __future__ import annotations

from .builtin import BUILTIN_TOOLS, register_builtins
from .exceptions import (
    ApprovalDenied,
    ArgsValidationError,
    ExecutorFailure,
    OverBudget,
    PendingApproval,
    RoleNotAllowed,
    ToolError,
    ToolPermissionDenied,
    UnknownTool,
)
from .models import (
    ApprovalRequirement,
    ToolCategory,
    ToolContext,
    ToolDef,
    ToolResult,
    ToolServices,
)
from .service import Tools

__all__ = [
    "BUILTIN_TOOLS",
    "ApprovalDenied",
    "ApprovalRequirement",
    "ArgsValidationError",
    "ExecutorFailure",
    "OverBudget",
    "PendingApproval",
    "RoleNotAllowed",
    "ToolCategory",
    "ToolContext",
    "ToolDef",
    "ToolError",
    "ToolPermissionDenied",
    "ToolResult",
    "ToolServices",
    "Tools",
    "UnknownTool",
    "register_builtins",
]
