"""``/companies/{id}/efficiency/*`` — listing and ack/close of findings."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from modules.efficiency import (
    Finding,
    FindingNotFound,
    FindingStatus,
    InvalidTransition,
    Severity,
)
from modules.factory import CompanyNotFound

from ..auth.deps import (
    AuthenticatedUser,
    require_decider,
    require_user,
)
from ..deps import get_runtime
from ..runtime import BoardRuntime
from ._helpers import (
    audit_record,
    conflict,
    ensure_company_access,
    not_found,
)


class FindingResponse(BaseModel):
    id: str
    company_id: str
    detector_code: str
    severity: str
    subject_type: str
    subject_id: str | None
    opened_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
    occurrences: int
    evidence: dict
    recommendation: str | None
    status: str


def _to_response(company_id: str, f: Finding) -> FindingResponse:
    return FindingResponse(
        id=f.id,
        company_id=company_id,
        detector_code=f.detector_code,
        severity=f.severity.value,
        subject_type=f.subject_type.value,
        subject_id=f.subject_id,
        opened_at=f.opened_at,
        last_seen_at=f.last_seen_at,
        closed_at=f.closed_at,
        occurrences=f.occurrences,
        evidence=f.evidence,
        recommendation=f.recommendation,
        status=f.status.value,
    )


def _parse_severities(raw: str | None) -> list[Severity] | None:
    if not raw:
        return None
    out: list[Severity] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(Severity(token))
        except ValueError:
            continue
    return out or None


def _parse_status(raw: str | None) -> FindingStatus | None:
    if not raw:
        return None
    try:
        return FindingStatus(raw)
    except ValueError:
        return None


def build_efficiency_router() -> APIRouter:
    router = APIRouter(tags=["efficiency"])

    @router.get(
        "/companies/{company_id}/efficiency/findings",
        response_model=list[FindingResponse],
    )
    async def list_findings(
        company_id: str,
        status: str | None = Query(default=None),
        severity: str | None = Query(default=None),
        detector_code: str | None = Query(default=None),
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> list[FindingResponse]:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if handle.efficiency is None:
            return []
        findings = handle.efficiency.store.list(
            status=_parse_status(status),
            severities=_parse_severities(severity),
            detector_code=detector_code,
        )
        return [_to_response(company_id, f) for f in findings]

    @router.get(
        "/companies/{company_id}/efficiency/findings/{finding_id}",
        response_model=FindingResponse,
    )
    async def get_finding(
        company_id: str,
        finding_id: str,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> FindingResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if handle.efficiency is None:
            raise not_found("efficiency service unavailable", code="efficiency_off")
        try:
            finding = handle.efficiency.store.get(finding_id)
        except FindingNotFound as exc:
            raise not_found(str(exc), code="finding_not_found") from exc
        return _to_response(company_id, finding)

    @router.post(
        "/companies/{company_id}/efficiency/findings/{finding_id}/ack",
        response_model=FindingResponse,
    )
    async def ack_finding(
        company_id: str,
        finding_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_decider),
    ) -> FindingResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if handle.efficiency is None:
            raise not_found("efficiency service unavailable", code="efficiency_off")
        try:
            handle.efficiency.acknowledge(finding_id)
            finding = handle.efficiency.store.get(finding_id)
        except FindingNotFound as exc:
            raise not_found(str(exc), code="finding_not_found") from exc
        except InvalidTransition as exc:
            raise conflict(str(exc), code="invalid_transition") from exc
        audit_record(
            runtime.audit,
            user_id=current.id,
            action="efficiency.finding.ack",
            request=request,
            target=finding_id,
            payload={"company_id": company_id},
        )
        return _to_response(company_id, finding)

    @router.post(
        "/companies/{company_id}/efficiency/findings/{finding_id}/close",
        response_model=FindingResponse,
    )
    async def close_finding(
        company_id: str,
        finding_id: str,
        request: Request,
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_decider),
    ) -> FindingResponse:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc
        if handle.efficiency is None:
            raise not_found("efficiency service unavailable", code="efficiency_off")
        try:
            handle.efficiency.manual_close(finding_id)
            finding = handle.efficiency.store.get(finding_id)
        except FindingNotFound as exc:
            raise not_found(str(exc), code="finding_not_found") from exc
        audit_record(
            runtime.audit,
            user_id=current.id,
            action="efficiency.finding.close",
            request=request,
            target=finding_id,
            payload={"company_id": company_id},
        )
        return _to_response(company_id, finding)

    return router


__all__ = ["FindingResponse", "build_efficiency_router"]
