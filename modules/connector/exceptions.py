"""Connector exception hierarchy."""
from __future__ import annotations


class ConnectorError(Exception):
    code: str = "connector_error"


class ServiceNotAllowed(ConnectorError):
    code = "not_allowed"


class UnknownService(ConnectorError):
    code = "unknown_service"


class UnknownAction(ConnectorError):
    code = "unknown_action"


class MissingCredential(ConnectorError):
    code = "missing_credential"


class MalformedResponse(ConnectorError):
    code = "malformed_response"


class RateLimited(ConnectorError):
    code = "rate_limited"

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(f"retry_after={retry_after_seconds:.2f}s")
        self.retry_after_seconds = retry_after_seconds


class ExternalServiceFailure(ConnectorError):
    code = "external_failure"


class PendingApprovalError(ConnectorError):
    code = "pending_approval"

    def __init__(self, request_id: str) -> None:
        super().__init__(f"pending:{request_id}")
        self.request_id = request_id


class ApprovalDeniedError(ConnectorError):
    code = "approval_denied"

    def __init__(self, request_id: str) -> None:
        super().__init__(f"denied:{request_id}")
        self.request_id = request_id
