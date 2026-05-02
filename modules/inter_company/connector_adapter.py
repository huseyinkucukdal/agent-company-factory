"""Connector ServiceDef adapter for inter-company messaging.

This bridges :class:`InterCompanyService` to :class:`modules.connector.Connector`
so an agent can issue a cross-company message via the same idempotent,
audited pipeline used for any other external service.

Usage at company bootstrap::

    handle.connector.register_service(
        build_inter_company_service(
            from_company=handle.company_id,
            inter_company=runtime.inter_company,
        ),
    )

Trust boundary: the executor receives ``args.from_agent`` from the agent.
The connector additionally records the *real* calling agent on the
``EventKind.EXTERNAL_CALL`` audit row (``actor`` field), which is the
source of truth for forensics.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from modules.connector.models import (
    ActionDef,
    AuthMethod,
    RateLimit,
    RiskLevel,
    ServiceDef,
)
from modules.cost import Money

from .exceptions import (
    CrossRateLimited,
    DirectionMismatch,
    InterCompanyError,
    LinkNotApproved,
    LinkNotFound,
    PayloadTooLarge,
    ScopeViolation,
    TargetUnavailable,
)
from .models import CrossMessage, CrossMessageKind, CrossResult, CrossStatus

if TYPE_CHECKING:
    from .service import InterCompanyService


# Conservative defaults; the per-link scope still enforces its own
# stricter limit. The connector-side rate limit only protects the
# service from a runaway agent burst.
_DEFAULT_RATE = RateLimit(capacity=120, refill_per_second=120 / 3600)


class InterCompanySendArgs(BaseModel):
    """Arguments accepted by the ``inter_company.send`` action."""

    link_id: str = Field(..., min_length=1)
    from_agent: str = Field(..., min_length=1)
    kind: CrossMessageKind = CrossMessageKind.GENERIC
    subject: str = Field(default="", max_length=512)
    body: str = Field(...)
    metadata: dict[str, object] = Field(default_factory=dict)


class InterCompanySendResult(BaseModel):
    id: str
    link_id: str
    status: CrossStatus
    target_agent: str | None = None
    reason: str | None = None


def build_inter_company_service(
    *,
    from_company: str,
    inter_company: InterCompanyService,
    rate_limit: RateLimit | None = None,
) -> ServiceDef:
    """Construct a :class:`ServiceDef` for the calling company.

    The returned definition is **company-scoped**: ``from_company`` is
    captured in the executor closure so an agent cannot forge a different
    origin even if its caller does. ``link_id`` is provided per call.
    """

    def _zero_cost(_args: InterCompanySendArgs) -> Money:
        return Money(Decimal("0"))

    def _identity_sanitizer(value: object) -> InterCompanySendResult:
        # Executor already returns the model.
        if isinstance(value, InterCompanySendResult):
            return value
        # Defensive: accept dicts too.
        return InterCompanySendResult.model_validate(value)

    async def _executor(args: InterCompanySendArgs) -> InterCompanySendResult:
        try:
            result: CrossResult = await inter_company.send_cross(
                link_id=args.link_id,
                from_agent=args.from_agent,
                from_company=from_company,
                message=CrossMessage(
                    kind=args.kind,
                    subject=args.subject,
                    body=args.body,
                    metadata=dict(args.metadata),
                ),
            )
        except (
            LinkNotFound,
            LinkNotApproved,
            DirectionMismatch,
            ScopeViolation,
            PayloadTooLarge,
            CrossRateLimited,
            TargetUnavailable,
        ) as exc:
            # Surface as a structured failure result so the connector
            # records a normal completion (not a retryable error).
            return InterCompanySendResult(
                id="",
                link_id=args.link_id,
                status=CrossStatus.REJECTED,
                target_agent=None,
                reason=f"{type(exc).__name__}:{exc}",
            )
        return InterCompanySendResult(
            id=result.id,
            link_id=result.link_id,
            status=result.status,
            target_agent=result.target_agent,
            reason=result.reason,
        )

    send = ActionDef(
        description=(
            "Send a structured message to another company over an "
            "approved board-managed link."
        ),
        args_schema=InterCompanySendArgs,
        output_schema=InterCompanySendResult,
        cost_estimator=_zero_cost,
        executor=_executor,
        sanitizer=_identity_sanitizer,
        rate_limit=rate_limit or _DEFAULT_RATE,
        auto_approve_threshold=Money(Decimal("0")),
        idempotent=True,
    )

    return ServiceDef(
        name="inter_company",
        description="Cross-company communication over approved links.",
        auth=AuthMethod.NONE,
        risk=RiskLevel.MEDIUM,
        actions={"send": send},
    )


__all__ = [
    "InterCompanySendArgs",
    "InterCompanySendResult",
    "build_inter_company_service",
]
