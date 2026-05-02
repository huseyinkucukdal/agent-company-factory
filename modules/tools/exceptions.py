"""Tool-layer exception hierarchy."""
from __future__ import annotations


class ToolError(Exception):
    """Base class for tool-invocation errors."""

    code: str = "tool_error"


class UnknownTool(ToolError):
    code = "unknown_tool"


class RoleNotAllowed(ToolError):
    code = "role_not_allowed"


class ArgsValidationError(ToolError):
    code = "args_validation_error"


class OverBudget(ToolError):
    code = "over_budget"


class PendingApproval(ToolError):
    code = "pending_approval"

    def __init__(self, request_id: str) -> None:
        super().__init__(f"pending:{request_id}")
        self.request_id = request_id


class ApprovalDenied(ToolError):
    code = "approval_denied"

    def __init__(self, request_id: str) -> None:
        super().__init__(f"denied:{request_id}")
        self.request_id = request_id


class ToolPermissionDenied(ToolError):
    code = "permission_denied"


class ExecutorFailure(ToolError):
    code = "executor_failure"
