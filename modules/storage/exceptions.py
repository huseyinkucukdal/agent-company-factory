"""Exceptions raised by the Storage module."""


class StorageError(Exception):
    """Base for all Storage module errors."""


class QuotaExceeded(StorageError):
    """A write would exceed an agent or company quota."""


class PermissionDenied(StorageError):
    """Reader is not permitted to access this workspace."""


class PathOutsideWorkspace(StorageError):
    """The given relative path resolves outside the workspace base."""


class CompanyNotFound(StorageError):
    """The given company_id is not registered."""


class CorruptDatabase(StorageError):
    """The SQLite file failed integrity checks."""


class WorkspaceNotInitialized(StorageError):
    """The agent's workspace has not been created."""
