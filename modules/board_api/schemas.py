"""Pydantic request/response schemas for the Board API.

Kept separate from the route handlers so they're easy to import from
tests and from any future client-generation pipeline.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from .rbac import UserRole

# --------------------------------------------------------------------- auth


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=8)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int


class UserResponse(BaseModel):
    id: str
    email: str
    role: UserRole
    created_at: datetime
    last_login: datetime | None = None


class LoginResponse(BaseModel):
    user: UserResponse
    token: TokenResponse


# -------------------------------------------------------------------- users


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    role: UserRole = UserRole.OBSERVER


class UpdateUserRequest(BaseModel):
    role: UserRole


class AssignmentRequest(BaseModel):
    company_id: str = Field(min_length=1, max_length=64)


# ---------------------------------------------------------------- companies


class ExtraAgentSpecModel(BaseModel):
    role_title: str = Field(min_length=1, max_length=120)
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    role_description: str = ""
    persona_ref: str = "default"
    reports_to_role: str = "ceo"


class CompanyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mission: str = Field(min_length=1, max_length=2000)
    industry: str | None = None
    initial_budget_usd: Decimal = Field(gt=Decimal(0))
    company_disk_quota_mb: int = Field(gt=0, le=1_000_000)
    default_agent_quota_mb: int = Field(gt=0, le=1_000_000)
    auto_approve_threshold_usd: Decimal = Field(default=Decimal(0), ge=Decimal(0))
    extra_agents: list[ExtraAgentSpecModel] = Field(default_factory=list)
    company_id: str | None = None


class CompanyResponse(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    closed_at: datetime | None = None


class CompanyDetailResponse(CompanyResponse):
    mission: str | None = None
    industry: str | None = None
    clock_state: str | None = None
    company_time: datetime | None = None
    real_time: datetime | None = None


# ---------------------------------------------------------------- approvals


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "deny"]
    note: str | None = Field(default=None, max_length=2000)


class ApprovalResponse(BaseModel):
    request_id: str
    company_id: str
    kind: str
    requester_id: str
    payload: dict[str, Any]
    status: str
    route_target: str
    require_security: bool
    security_decision: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    note: str | None = None
    expires_at: datetime
    created_at: datetime


# ---------------------------------------------------------------- settings


class BudgetUpdateRequest(BaseModel):
    total_usd: Decimal = Field(gt=Decimal(0))


class BudgetStateResponse(BaseModel):
    total_usd: str
    spent_usd: str
    reserved_usd: str
    remaining_usd: str
    blocked: bool
    by_category: dict[str, str]


class AllowlistEntry(BaseModel):
    service: str
    action: str | None = None


class AllowlistUpdateRequest(BaseModel):
    allow: list[AllowlistEntry] = Field(default_factory=list)
    revoke: list[AllowlistEntry] = Field(default_factory=list)


class AllowlistResponse(BaseModel):
    entries: list[AllowlistEntry]


class ThresholdEntry(BaseModel):
    service: str
    action: str
    risk: str
    auto_approve_threshold_usd: str | None = None


class ThresholdsResponse(BaseModel):
    thresholds: list[ThresholdEntry]


class ThresholdUpdateRequest(BaseModel):
    service: str
    action: str
    auto_approve_threshold_usd: Decimal | None = None


class DiskQuotaUpdateRequest(BaseModel):
    company_quota_mb: int | None = Field(default=None, gt=0, le=1_000_000)
    agent_quota_mb: dict[str, int] | None = None


# ------------------------------------------------------------------ agents


class AgentResponse(BaseModel):
    id: str
    role: str
    persona_ref: str
    reports_to: str | None
    status: str
    hired_at: datetime
    first_name: str = ""
    last_name: str = ""
    role_title: str | None = None
    role_description: str | None = None
    fired_at: datetime | None = None
    fired_by: str | None = None
    fire_reason: str | None = None


class FeedbackResponse(BaseModel):
    id: str
    target_agent_id: str
    from_agent_id: str
    rating: int
    note: str
    ts: datetime


class PerformanceMetricsResponse(BaseModel):
    tasks_completed: int
    messages_sent: int
    messages_received: int
    tool_success_rate: float | None = None
    avg_response_seconds: float | None = None
    truncated_turns: int
    health_alerts: int
    tenure_days: int


class PerformanceResponse(BaseModel):
    metrics: PerformanceMetricsResponse
    recent_feedback: list[FeedbackResponse]


class FeedbackRequest(BaseModel):
    from_agent_id: str | None = Field(default=None, min_length=1)
    rating: int = Field(ge=1, le=5)
    note: str = Field(min_length=1, max_length=8_000)


class RoleChangeRequest(BaseModel):
    from_agent_id: str | None = Field(default=None, min_length=1)
    new_role_title: str = Field(min_length=1, max_length=120)
    new_role_description: str = Field(min_length=1, max_length=8_000)
    rationale: str = Field(min_length=1, max_length=8_000)


class RoleChangeResponse(BaseModel):
    status: str
    request_id: str | None = None


class FireAgentRequest(BaseModel):
    from_agent_id: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=1, max_length=8_000)


class FireAgentResponse(BaseModel):
    result: str
    reason: str | None = None
    request_id: str | None = None


class PerformanceReportDeliveryResponse(BaseModel):
    delivered_to: str
    first_name: str = ""
    last_name: str = ""
    role_title: str | None = None
    role_description: str | None = None


class FileMetaResponse(BaseModel):
    relative_path: str
    size_bytes: int
    modified_at: datetime
    is_dir: bool = False


class WorkspaceListingResponse(BaseModel):
    agent_id: str
    files: list[FileMetaResponse]


class WorkspaceFileResponse(BaseModel):
    agent_id: str
    relative_path: str
    content: str
    size_bytes: int


# --------------------------------------------------------------- expenses


class ExpenseResponse(BaseModel):
    id: str
    agent_id: str | None
    amount_usd: str
    category: str
    memo: str | None = None
    approval_id: str | None = None
    ts_company: datetime
    ts_real: datetime


# ----------------------------------------------------------------- events


class EventResponse(BaseModel):
    id: int
    company_id: str
    kind: str
    payload: dict[str, Any]
    actor_agent_id: str | None
    correlation_id: str | None
    ts_company: datetime
    ts_real: datetime


# -------------------------------------------------------------- llm stats


class LLMStatsResponse(BaseModel):
    total: int
    failed: int
    rate_limited: int
    last_provider: str | None = None
    last_model: str | None = None
    last_at: str | None = None


# ------------------------------------------------------------------ links


class LinkScopeModel(BaseModel):
    allowed_messages: list[str] = Field(default_factory=list)
    rate_limit_per_hour: int = Field(default=100, gt=0, le=10_000)
    max_payload_bytes: int = Field(default=65536, gt=0, le=1_048_576)


class LinkRequestRequest(BaseModel):
    from_company: str = Field(min_length=1, max_length=64)
    to_company: str = Field(min_length=1, max_length=64)
    relationship: Literal["peer", "vendor", "customer"] = "peer"
    scope: LinkScopeModel = Field(default_factory=LinkScopeModel)


class LinkResponse(BaseModel):
    id: str
    from_company: str
    to_company: str
    relationship: str
    scope: LinkScopeModel
    status: str
    requested_by: str
    requested_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    note: str | None = None


class LinkDecisionRequest(BaseModel):
    decision: Literal["approve", "deny"]
    note: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------------------ audit


class AuditResponse(BaseModel):
    id: int
    user_id: str
    action: str
    target: str | None
    payload: dict[str, Any]
    ts: datetime
    ip: str | None = None
    user_agent: str | None = None


# --------------------------------------------------------------- generic


class ErrorResponse(BaseModel):
    error: str
    code: str
    details: dict[str, Any] | None = None


class OkResponse(BaseModel):
    ok: bool = True


__all__ = [
    "AgentResponse",
    "AllowlistEntry",
    "AllowlistResponse",
    "AllowlistUpdateRequest",
    "ApprovalDecisionRequest",
    "ApprovalResponse",
    "AssignmentRequest",
    "AuditResponse",
    "BudgetStateResponse",
    "BudgetUpdateRequest",
    "CompanyCreateRequest",
    "CompanyDetailResponse",
    "CompanyResponse",
    "CreateUserRequest",
    "DiskQuotaUpdateRequest",
    "ErrorResponse",
    "EventResponse",
    "ExpenseResponse",
    "ExtraAgentSpecModel",
    "FileMetaResponse",
    "FeedbackRequest",
    "FeedbackResponse",
    "FireAgentRequest",
    "FireAgentResponse",
    "LinkDecisionRequest",
    "LinkRequestRequest",
    "LinkResponse",
    "LinkScopeModel",
    "LoginRequest",
    "LoginResponse",
    "LogoutRequest",
    "OkResponse",
    "PerformanceMetricsResponse",
    "PerformanceReportDeliveryResponse",
    "PerformanceResponse",
    "RefreshRequest",
    "RegisterRequest",
    "RoleChangeRequest",
    "RoleChangeResponse",
    "ThresholdEntry",
    "ThresholdUpdateRequest",
    "ThresholdsResponse",
    "TokenResponse",
    "UpdateUserRequest",
    "UserResponse",
    "WorkspaceFileResponse",
    "WorkspaceListingResponse",
]
