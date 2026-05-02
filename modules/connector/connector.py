"""Connector — single-doorway gateway to external services.

Pipeline (10 steps for ``call``):

1. Idempotency cache lookup by ``correlation_id``.
2. Service + action lookup → :class:`UnknownService` / :class:`UnknownAction`.
3. Allowlist gate → :class:`ServiceNotAllowed`.
4. Args validation via the action's pydantic schema.
5. Cost estimate.
6. Rate-limit (per-service token bucket).
7. Auto-approve evaluation: LOW + cost ≤ threshold → run; otherwise route
   through :class:`Approvals`. CRITICAL forces ``require_security=True``.
   First call returns ``approval_request_id`` and pending status; second
   call with the same ``correlation_id`` proceeds when APPROVED, raises
   :class:`ApprovalDeniedError` when DENIED.
8. Cost reservation.
9. Executor invocation (with retry for idempotent actions on transient
   :class:`ExternalServiceFailure`). Output passes through the action's
   ``sanitizer`` before being returned.
10. Commit reservation + emit ``EventKind.EXTERNAL_CALL`` audit event.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
import threading
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    Approvals,
    ApprovalStatus,
    RouteTarget,
)
from modules.cost import Budget, Money
from modules.cost import OverBudget as CostOverBudget
from modules.event_store import EventKind, EventStore

from .allowlist import Allowlist
from .exceptions import (
    ApprovalDeniedError,
    ConnectorError,
    ExternalServiceFailure,
    RateLimited,
    ServiceNotAllowed,
    UnknownAction,
    UnknownService,
)
from .models import (
    ActionDef,
    ConnectorResult,
    RiskLevel,
    ServiceDef,
)
from .rate_limit import RateLimiter
from .secrets import Secrets

_log = logging.getLogger(__name__)

_RETRYABLE_LIMIT = 3


class Connector:
    """Single-door external gateway."""

    def __init__(
        self,
        *,
        cost: Budget,
        approvals: Approvals,
        events: EventStore,
        secrets: Secrets,
        allowlist: Allowlist | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._cost = cost
        self._approvals = approvals
        self._events = events
        self._secrets = secrets
        self._allowlist = allowlist or Allowlist()
        self._rate = rate_limiter or RateLimiter()
        self._services: dict[str, ServiceDef] = {}
        self._cache_lock = threading.Lock()
        self._completed: dict[str, ConnectorResult] = {}

    # ------------------------------------------------------------- registry

    def register_service(self, service: ServiceDef) -> None:
        if service.name in self._services:
            raise ValueError(f"service already registered: {service.name}")
        self._services[service.name] = service
        # Plug rate limits.
        for action_name, action in service.actions.items():
            if action.rate_limit is not None:
                self._rate.configure(
                    f"{service.name}:{action_name}", action.rate_limit
                )

    def get_service(self, name: str) -> ServiceDef:
        try:
            return self._services[name]
        except KeyError as exc:
            raise UnknownService(name) from exc

    def is_allowed(self, service_name: str, action: str) -> bool:
        return self._allowlist.is_allowed(service_name, action)

    @property
    def allowlist(self) -> Allowlist:
        return self._allowlist

    @property
    def secrets(self) -> Secrets:
        return self._secrets

    # ---------------------------------------------------------- thresholds

    def list_thresholds(self) -> list[dict[str, Any]]:
        """Snapshot of every action's auto-approve threshold (USD)."""
        out: list[dict[str, Any]] = []
        for svc_name in sorted(self._services):
            svc = self._services[svc_name]
            for action_name in sorted(svc.actions):
                action = svc.actions[action_name]
                threshold = action.auto_approve_threshold
                out.append(
                    {
                        "service": svc_name,
                        "action": action_name,
                        "risk": svc.risk.value,
                        "auto_approve_threshold_usd": (
                            str(threshold.amount_usd)
                            if threshold is not None
                            else None
                        ),
                    }
                )
        return out

    def set_action_threshold(
        self, service_name: str, action_name: str, threshold: Money | None,
    ) -> None:
        """Override (or clear) the auto-approve threshold for one action.

        Mutates the :class:`ActionDef` in-place via ``dataclasses.replace``.
        Concurrent ``call`` invocations see the new value on their next
        ``_evaluate_approval`` step.
        """
        svc = self.get_service(service_name)
        if action_name not in svc.actions:
            raise UnknownAction(f"{service_name}:{action_name}")
        old = svc.actions[action_name]
        svc.actions[action_name] = dataclasses.replace(
            old, auto_approve_threshold=threshold,
        )

    # ------------------------------------------------------------------ call

    async def call(
        self,
        *,
        agent_id: str,
        service: str,
        action: str,
        args: dict[str, Any],
        correlation_id: str | None = None,
    ) -> ConnectorResult:
        cid = correlation_id or uuid.uuid4().hex

        with self._cache_lock:
            cached = self._completed.get(cid)
        if cached is not None:
            return cached

        # ---- registry lookup
        try:
            svc = self.get_service(service)
        except UnknownService as exc:
            return self._fail(cid, service, action, exc, agent_id, charge=False)
        action_def = svc.actions.get(action)
        if action_def is None:
            return self._fail(
                cid, service, action,
                UnknownAction(f"{service}.{action}"),
                agent_id, charge=False,
            )

        # ---- allowlist
        if not self._allowlist.is_allowed(service, action):
            return self._fail(
                cid, service, action,
                ServiceNotAllowed(f"{service}.{action}"),
                agent_id, charge=False,
            )

        # ---- args validation
        try:
            parsed = action_def.args_schema.model_validate(args)
        except ValidationError as exc:
            return self._fail(
                cid, service, action,
                ConnectorError(f"args:{exc}"),
                agent_id, charge=False,
            )

        # ---- cost estimate
        estimate = action_def.cost_estimator(parsed)

        # ---- rate limit
        try:
            self._rate.acquire(f"{service}:{action}")
        except RateLimited as exc:
            return self._fail(cid, service, action, exc, agent_id, charge=False)

        # ---- auto-approve / approval routing
        decision = self._evaluate_approval(
            svc=svc, action_def=action_def, agent_id=agent_id,
            estimate=estimate, cid=cid, args=args, action_name=action,
        )
        if decision == "pending":
            # Don't cache; the agent must retry once the approval resolves.
            self._emit_event(
                service=service, action=action, status="issued",
                actor=agent_id, cid=cid,
            )
            return ConnectorResult(
                ok=False,
                error_code="pending_approval",
                error_message=f"pending:{cid}",
                approval_request_id=cid,
            )
        if decision == "denied":
            return self._fail(
                cid, service, action,
                ApprovalDeniedError(cid),
                agent_id, charge=False,
            )

        # ---- cost reservation
        try:
            reservation = self._cost.reserve(estimate, ref=cid)
        except CostOverBudget as exc:
            return self._fail(
                cid, service, action,
                ConnectorError(f"over_budget:{exc}"),
                agent_id, charge=False,
            )

        # ---- execute (with retry for idempotent actions)
        try:
            output = await self._execute_with_retry(action_def, parsed)
        except ExternalServiceFailure as exc:
            self._cost.release(reservation.id)
            return self._fail(cid, service, action, exc, agent_id, charge=False)
        except Exception as exc:
            self._cost.release(reservation.id)
            return self._fail(
                cid, service, action,
                ExternalServiceFailure(str(exc)),
                agent_id, charge=False,
            )

        # ---- sanitize → must be the action's output_schema
        try:
            sanitized = action_def.sanitizer(output)
        except Exception as exc:
            self._cost.release(reservation.id)
            return self._fail(
                cid, service, action,
                ConnectorError(f"malformed:{exc}"),
                agent_id, charge=False,
            )
        if not isinstance(sanitized, action_def.output_schema):
            self._cost.release(reservation.id)
            return self._fail(
                cid, service, action,
                ConnectorError(
                    f"sanitizer returned {type(sanitized).__name__}, "
                    f"expected {action_def.output_schema.__name__}"
                ),
                agent_id, charge=False,
            )

        # ---- commit
        charge = self._cost.commit(reservation.id, estimate)
        if not charge.ok:
            return self._fail(
                cid, service, action,
                ConnectorError("commit_failed"),
                agent_id, charge=False,
            )

        result = ConnectorResult(
            ok=True, output=sanitized, cost_usd=estimate.amount_usd
        )
        self._emit_event(
            service=service, action=action, status="ok",
            actor=agent_id, cid=cid,
        )
        with self._cache_lock:
            self._completed[cid] = result
        return result

    # ------------------------------------------------------------- internals

    def _evaluate_approval(
        self,
        *,
        svc: ServiceDef,
        action_def: ActionDef,
        agent_id: str,
        estimate: Money,
        cid: str,
        args: dict[str, Any],
        action_name: str,
    ) -> str:
        # Auto-approve gate.
        if svc.risk is RiskLevel.LOW:
            threshold = action_def.auto_approve_threshold
            if threshold is not None and estimate <= threshold:
                return "approved"

        require_security = svc.risk is RiskLevel.CRITICAL
        approval = self._approvals.request(
            kind=ApprovalKind.EXTERNAL_ACTION,
            requester_id=agent_id,
            payload={
                "service": svc.name,
                "action": action_name,
                # The args are part of the audit trail; secrets are never
                # passed in by the caller (Connector resolves them later).
                "args": args,
                "estimated_cost_usd": str(estimate.amount_usd),
                "risk": svc.risk.value,
            },
            route=ApprovalRoute(
                target=RouteTarget.BOARD, require_security=require_security
            ),
            request_id=cid,
        )
        status: ApprovalStatus = approval.status
        if status is ApprovalStatus.APPROVED:
            return "approved"
        if status is ApprovalStatus.PENDING:
            return "pending"
        return "denied"

    async def _execute_with_retry(
        self, action_def: ActionDef, args: BaseModel
    ) -> Any:
        attempts = _RETRYABLE_LIMIT if action_def.idempotent else 1
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                value = action_def.executor(args)
                if inspect.isawaitable(value):
                    value = await value
                return value
            except ExternalServiceFailure as exc:
                last_exc = exc
                if attempt >= attempts:
                    raise
                await asyncio.sleep(0.01 * (2 ** (attempt - 1)))
        # Unreachable, but keeps mypy + future-proof
        raise last_exc or ExternalServiceFailure("unreachable")

    def _emit_event(
        self,
        *,
        service: str,
        action: str,
        status: str,
        actor: str,
        cid: str,
    ) -> None:
        # NOTE: event payloads must use the ``ExternalCallPayload`` shape
        # defined in :mod:`modules.event_store.payloads`.
        self._events.append(
            EventKind.EXTERNAL_CALL,
            {
                "service": service,
                "endpoint": action,
                "request_id": cid,
                "status": status,
            },
            actor=actor,
            correlation=cid,
        )

    def _fail(
        self,
        cid: str,
        service: str,
        action: str,
        exc: Exception,
        actor: str,
        *,
        charge: bool,
    ) -> ConnectorResult:
        code = getattr(exc, "code", "external_failure")
        result = ConnectorResult(
            ok=False,
            error_code=code,
            error_message=str(exc),
            cost_usd=Decimal("0.0000"),
            approval_request_id=getattr(exc, "request_id", None),
        )
        self._emit_event(
            service=service, action=action, status="error",
            actor=actor, cid=cid,
        )
        # Permanent failures are cached so retries don't re-trigger; transient
        # rate-limited / pending errors are NOT cached (handled earlier).
        if not isinstance(exc, RateLimited):
            with self._cache_lock:
                self._completed[cid] = result
        if not charge:
            return result
        return result


__all__ = ["Connector"]
