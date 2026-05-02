"""HTTP service (mock-friendly)."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from modules.cost import Money

from ..exceptions import ExternalServiceFailure, MalformedResponse
from ..models import ActionDef, AuthMethod, RateLimit, RiskLevel, ServiceDef
from ..sanitizer import safe_text


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# -------------------------------------------------------------------- schemas


class HttpRequestArgs(_Frozen):
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    timeout_seconds: float = 10.0


class HttpResponse(_Frozen):
    status: int
    headers: dict[str, str]
    body_preview: str
    body_length: int


# ---------------------------------------------------------------- HttpClient


class HttpClient(Protocol):
    def request(
        self, method: str, url: str, *,
        headers: Mapping[str, str], body: str | None, timeout: float,
    ) -> HttpClientResponse: ...


class HttpClientResponse(Protocol):
    status: int
    headers: Mapping[str, str]
    text: str


# ------------------------------------------------------------- factory builder


def _make_executor(client: HttpClient, method: str) -> Any:
    def _executor(args: HttpRequestArgs) -> HttpResponse:
        try:
            response = client.request(
                method, args.url,
                headers=args.headers, body=args.body,
                timeout=args.timeout_seconds,
            )
        except ExternalServiceFailure:
            raise
        except Exception as exc:
            raise ExternalServiceFailure(str(exc)) from exc
        if not isinstance(response.status, int):
            raise MalformedResponse(f"bad status type: {type(response.status)}")
        return HttpResponse(
            status=response.status,
            headers={str(k): str(v) for k, v in response.headers.items()},
            body_preview=safe_text(response.text, max_length=2_000),
            body_length=len(response.text),
        )

    return _executor


def _identity_sanitizer(value: HttpResponse) -> HttpResponse:
    if not isinstance(value, HttpResponse):
        raise MalformedResponse(f"expected HttpResponse, got {type(value)}")
    return value


def _flat_cost(_args: HttpRequestArgs) -> Money:
    return Money.of(Decimal("0.0001"))


def build_http_service(client: HttpClient) -> ServiceDef:
    rate = RateLimit(capacity=20, refill_per_second=10.0)
    actions: dict[str, ActionDef] = {}
    for method in ("get", "post", "put", "delete"):
        actions[method] = ActionDef(
            description=f"HTTP {method.upper()} request",
            args_schema=HttpRequestArgs,
            output_schema=HttpResponse,
            cost_estimator=_flat_cost,
            executor=_make_executor(client, method.upper()),
            sanitizer=_identity_sanitizer,
            rate_limit=rate,
            auto_approve_threshold=None,
            idempotent=method in ("get", "put", "delete"),
        )
    return ServiceDef(
        name="http",
        description="Generic HTTP client",
        auth=AuthMethod.NONE,
        risk=RiskLevel.MEDIUM,
        actions=actions,
    )


__all__ = [
    "HttpClient",
    "HttpClientResponse",
    "HttpRequestArgs",
    "HttpResponse",
    "build_http_service",
]
