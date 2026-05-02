"""Email service (mock-friendly)."""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from modules.cost import Money

from ..exceptions import ExternalServiceFailure
from ..models import ActionDef, AuthMethod, RateLimit, RiskLevel, ServiceDef


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
EmailAddress = Annotated[str, Field(pattern=_EMAIL_RE)]


class EmailSendArgs(_Frozen):
    to: list[EmailAddress] = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)


class EmailAttachment(_Frozen):
    filename: str
    media_type: str
    size_bytes: int


class EmailSendWithAttachmentArgs(EmailSendArgs):
    attachments: list[EmailAttachment] = Field(min_length=1)


class EmailSendResult(_Frozen):
    message_id: str
    accepted: list[str]


class EmailClient(Protocol):
    def send(
        self, *, sender: str, to: Sequence[str],
        subject: str, body: str,
        attachments: Sequence[EmailAttachment] = (),
    ) -> EmailClientResponse: ...


class EmailClientResponse(Protocol):
    message_id: str
    accepted: Sequence[str]


def _identity_sanitizer(value: EmailSendResult) -> EmailSendResult:
    if not isinstance(value, EmailSendResult):
        raise ValueError("expected EmailSendResult")
    return value


def _send_cost(_args: EmailSendArgs) -> Money:
    return Money.of(Decimal("0.0010"))


def _attachment_cost(args: EmailSendWithAttachmentArgs) -> Money:
    return Money.of(Decimal("0.0010") + Decimal("0.0005") * len(args.attachments))


def _make_executor(
    client: EmailClient, sender: str, with_attachments: bool
) -> Any:
    def _executor(args: EmailSendArgs) -> EmailSendResult:
        attachments: Sequence[EmailAttachment] = ()
        if with_attachments and isinstance(args, EmailSendWithAttachmentArgs):
            attachments = args.attachments
        try:
            response = client.send(
                sender=sender, to=list(args.to),
                subject=args.subject, body=args.body,
                attachments=attachments,
            )
        except ExternalServiceFailure:
            raise
        except Exception as exc:
            raise ExternalServiceFailure(str(exc)) from exc
        return EmailSendResult(
            message_id=str(response.message_id),
            accepted=[str(addr) for addr in response.accepted],
        )

    return _executor


def build_email_service(client: EmailClient, *, sender: str) -> ServiceDef:
    rate = RateLimit(capacity=30, refill_per_second=2.0)
    actions: dict[str, ActionDef] = {
        "send": ActionDef(
            description="Send a plain email",
            args_schema=EmailSendArgs,
            output_schema=EmailSendResult,
            cost_estimator=_send_cost,
            executor=_make_executor(client, sender, with_attachments=False),
            sanitizer=_identity_sanitizer,
            rate_limit=rate,
            auto_approve_threshold=None,
            idempotent=False,
        ),
        "send_with_attachment": ActionDef(
            description="Send an email with attachments",
            args_schema=EmailSendWithAttachmentArgs,
            output_schema=EmailSendResult,
            cost_estimator=_attachment_cost,
            executor=_make_executor(client, sender, with_attachments=True),
            sanitizer=_identity_sanitizer,
            rate_limit=rate,
            auto_approve_threshold=None,
            idempotent=False,
        ),
    }
    return ServiceDef(
        name="email",
        description="Transactional email",
        auth=AuthMethod.API_KEY,
        risk=RiskLevel.MEDIUM,
        actions=actions,
    )


__all__ = [
    "EmailAttachment",
    "EmailClient",
    "EmailClientResponse",
    "EmailSendArgs",
    "EmailSendResult",
    "EmailSendWithAttachmentArgs",
    "build_email_service",
]
