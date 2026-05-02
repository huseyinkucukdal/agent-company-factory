"""Storage module: per-company SQLite + per-agent workspaces with quota.

See ``PLAN.md`` for the full design.
"""
from .db import BoardDB, CompanyDB
from .exceptions import (
    CompanyNotFound,
    CorruptDatabase,
    PathOutsideWorkspace,
    PermissionDenied,
    QuotaExceeded,
    StorageError,
    WorkspaceNotInitialized,
)
from .protocols import EventSink, IdentityProvider
from .quota import AgentQuota, CompanyQuota, Quota, QuotaScope
from .workspace import FileMeta, PROJECT_WORKSPACE_ID, Workspace, WriteResult

__all__ = [
    "AgentQuota",
    "BoardDB",
    "CompanyDB",
    "CompanyNotFound",
    "CompanyQuota",
    "CorruptDatabase",
    "EventSink",
    "FileMeta",
    "IdentityProvider",
    "PathOutsideWorkspace",
    "PermissionDenied",
    "PROJECT_WORKSPACE_ID",
    "Quota",
    "QuotaExceeded",
    "QuotaScope",
    "StorageError",
    "Workspace",
    "WorkspaceNotInitialized",
    "WriteResult",
]
