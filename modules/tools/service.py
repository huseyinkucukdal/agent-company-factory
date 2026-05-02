"""Registry + invoke pipeline for the Tool Layer.

``Tools.invoke`` is the single entry point. The pipeline is:

1. Lookup → :class:`UnknownTool` if missing.
2. Role gate → :class:`RoleNotAllowed` for forbidden roles.
3. Args validation via the tool's ``schema`` (pydantic).
4. Tool-specific :func:`permission_check`.
5. Cost estimate → :meth:`CostProvider.reserve` (raises :class:`OverBudget`).
6. Approval (if any) — first call returns :class:`PendingApproval`; second
   call with the same ``correlation_id`` checks the result. Reservation is
   released between rounds so the budget isn't held hostage.
7. Execute, commit reservation at actual cost, emit ``TOOL_RESULT``.
   Any executor exception → release + emit error result.

Idempotency: completed results are cached by ``correlation_id`` so a re-issued
call with the same id gets the cached :class:`ToolResult`.
"""
from __future__ import annotations

import inspect
import logging
import threading
import uuid
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    ApprovalStatus,
    RouteTarget,
)
from modules.cost import (
    Money,
)
from modules.cost import (
    OverBudget as CostOverBudget,
)
from modules.event_store import EventKind
from modules.identity import Role, Status

from .exceptions import (
    ApprovalDenied,
    ArgsValidationError,
    ExecutorFailure,
    OverBudget,
    PendingApproval,
    RoleNotAllowed,
    ToolError,
    ToolPermissionDenied,
    UnknownTool,
)
from .models import (
    ApprovalRequirement,
    ToolContext,
    ToolDef,
    ToolResult,
    ToolServices,
)
from .protocols import (
    ApprovalsProvider,
    Connector,
    CostProvider,
    EventSink,
    IdentityProvider,
    MemoryProvider,
    WorkspaceProvider,
)

_log = logging.getLogger(__name__)

_APPROVAL_KIND_FOR: dict[ApprovalRequirement, ApprovalKind] = {
    ApprovalRequirement.EXPENSE: ApprovalKind.EXPENSE,
    ApprovalRequirement.EXTERNAL: ApprovalKind.EXTERNAL_ACTION,
}


class Tools:
    """Tool registry + invocation pipeline."""

    def __init__(
        self,
        *,
        identity: IdentityProvider,
        cost: CostProvider,
        approvals: ApprovalsProvider,
        workspace: WorkspaceProvider,
        memory: MemoryProvider,
        events: EventSink,
        connector: Connector | None = None,
        performance: Any | None = None,
    ) -> None:
        self._defs: dict[str, ToolDef] = {}
        self._services = ToolServices(
            identity=identity,
            workspace=workspace,
            memory=memory,
            events=events,
            connector=connector,
            cost=cost,
            approvals=approvals,
            performance=performance,
            orchestrator=None,
        )
        self._cost = cost
        self._approvals = approvals
        self._events = events
        self._identity = identity

        self._cache_lock = threading.Lock()
        self._completed: dict[str, ToolResult] = {}

    def set_orchestrator(self, orchestrator: Any) -> None:
        """Late-bind the orchestrator. Called by the factory once both the
        Tools layer and the Orchestrator have been constructed.
        """
        self._services.orchestrator = orchestrator

    def set_hire_service(self, hire_service: Any) -> None:
        """Late-bind runtime hiring.

        A hire creates a live agent, not just an identity row. The factory owns
        the runtime wiring, so the tool layer receives this collaborator after
        the company handle exists.
        """
        self._services.hire_service = hire_service

    # --------------------------------------------------------------- registry

    def register(self, tool: ToolDef) -> None:
        if tool.name in self._defs:
            raise ValueError(f"tool already registered: {tool.name}")
        if tool.executor is None:
            raise ValueError(f"tool has no executor: {tool.name}")
        self._defs[tool.name] = tool

    def for_role(self, role: Role) -> list[ToolDef]:
        return [t for t in self._defs.values() if t.is_role_allowed(role)]

    def get(self, name: str) -> ToolDef:
        try:
            return self._defs[name]
        except KeyError as exc:
            raise UnknownTool(name) from exc

    def all_tools(self) -> list[ToolDef]:
        return list(self._defs.values())

    # ----------------------------------------------------------- claude SDK

    def claude_sdk_schema(self, role: Role) -> list[dict[str, Any]]:
        """Render allowed tools as Claude Agent SDK ``tools=[...]`` entries.

        Each entry is ``{"name", "description", "input_schema"}``; the schema
        is the pydantic model's JSON schema.
        """
        out: list[dict[str, Any]] = []
        for t in self.for_role(role):
            out.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.schema.model_json_schema(),
                }
            )
        return out

    # ------------------------------------------------------------------- invoke

    async def invoke(
        self,
        *,
        agent_id: str,
        tool_name: str,
        args: dict[str, Any],
        correlation_id: str | None = None,
    ) -> ToolResult:
        cid = correlation_id or uuid.uuid4().hex

        # ---- idempotency cache.
        with self._cache_lock:
            cached = self._completed.get(cid)
        if cached is not None:
            return cached

        try:
            tool = self.get(tool_name)
        except UnknownTool as exc:
            return self._fail(
                cid,
                tool_name,
                exc,
                actor=agent_id,
                emit_kind=EventKind.TOOL_DENIED,
            )

        # ---- agent + role gate.
        agent = self._identity.get(agent_id)
        if agent.status is not Status.ACTIVE:
            return self._fail(
                cid, tool.name,
                ToolPermissionDenied(f"agent_not_active:{agent_id}"),
                actor=agent_id,
                emit_kind=EventKind.TOOL_DENIED,
            )
        if not tool.is_role_allowed(agent.role):
            return self._fail(
                cid, tool.name,
                RoleNotAllowed(f"{agent.role.value}:{tool.name}"),
                actor=agent_id,
                emit_kind=EventKind.TOOL_DENIED,
            )

        # ---- args validation.
        try:
            parsed = tool.schema.model_validate(args)
        except ValidationError as exc:
            return self._fail(
                cid, tool.name, ArgsValidationError(str(exc)),
                actor=agent_id, emit_kind=EventKind.TOOL_DENIED,
            )

        ctx = ToolContext(
            agent_id=agent_id,
            role=agent.role,
            correlation_id=cid,
            services=self._services,
        )

        # ---- permission check.
        check = tool.permission_check
        if check is not None:
            try:
                check(ctx, parsed)
            except ToolError as exc:
                return self._fail(
                    cid, tool.name, exc, actor=agent_id,
                    emit_kind=EventKind.TOOL_DENIED,
                )

        # ---- cost reservation.
        estimate: Money = (
            tool.cost_estimator(parsed)
            if tool.cost_estimator is not None
            else Money.zero()
        )
        try:
            reservation = self._cost.reserve(estimate, ref=cid)
        except CostOverBudget as exc:
            return self._fail(
                cid, tool.name, OverBudget(str(exc)), actor=agent_id,
                emit_kind=EventKind.TOOL_DENIED,
            )

        # ---- approval (optional).
        if tool.requires_approval is not ApprovalRequirement.NONE:
            decision = self._handle_approval(
                tool=tool,
                agent_id=agent_id,
                cid=cid,
                args=args,
            )
            if decision == "pending":
                self._cost.release(reservation.id)
                return self._emit_only(
                    cid, tool.name, PendingApproval(cid), actor=agent_id
                )
            if decision == "denied":
                self._cost.release(reservation.id)
                return self._fail(
                    cid, tool.name, ApprovalDenied(cid), actor=agent_id,
                    emit_kind=EventKind.TOOL_DENIED,
                )

        # ---- announce execution.
        self._events.append(
            EventKind.TOOL_CALLED,
            {
                "tool": tool.name,
                "arguments": args,
                "request_id": cid,
            },
            actor=agent_id,
            correlation=cid,
        )

        # ---- execute.
        try:
            executor = tool.executor
            assert executor is not None
            output = await _maybe_await(executor(ctx, parsed))
        except ToolError as exc:
            self._cost.release(reservation.id)
            return self._fail(
                cid, tool.name, exc, actor=agent_id,
                emit_kind=EventKind.TOOL_RESULT, ok=False,
            )
        except Exception as exc:
            self._cost.release(reservation.id)
            return self._fail(
                cid, tool.name, ExecutorFailure(str(exc)),
                actor=agent_id,
                emit_kind=EventKind.TOOL_RESULT, ok=False,
            )

        if not isinstance(output, tool.output_schema):
            self._cost.release(reservation.id)
            return self._fail(
                cid, tool.name,
                ExecutorFailure(
                    f"executor returned {type(output).__name__}, "
                    f"expected {tool.output_schema.__name__}"
                ),
                actor=agent_id,
                emit_kind=EventKind.TOOL_RESULT, ok=False,
            )

        # ---- commit at actual cost (default = estimate).
        actual = estimate
        charge = self._cost.commit(reservation.id, actual)
        if not charge.ok:
            return self._fail(
                cid, tool.name, OverBudget(f"commit_failed:{cid}"),
                actor=agent_id,
                emit_kind=EventKind.TOOL_RESULT, ok=False,
            )

        result = ToolResult(
            ok=True,
            output=output,
            cost_usd=actual.amount_usd,
        )
        self._events.append(
            EventKind.TOOL_RESULT,
            {
                "tool": tool.name,
                "request_id": cid,
                "ok": True,
                "result": output.model_dump(mode="json"),
            },
            actor=agent_id,
            correlation=cid,
        )
        with self._cache_lock:
            self._completed[cid] = result
        return result

    # --------------------------------------------------------------- helpers

    def _handle_approval(
        self,
        *,
        tool: ToolDef,
        agent_id: str,
        cid: str,
        args: dict[str, Any],
    ) -> str:
        kind = _APPROVAL_KIND_FOR[tool.requires_approval]
        approval = self._approvals.request(
            kind=kind,
            requester_id=agent_id,
            payload={"tool": tool.name, "args": args},
            route=ApprovalRoute(RouteTarget.BOARD),
            request_id=cid,
        )
        status: ApprovalStatus = approval.status
        if status is ApprovalStatus.APPROVED:
            return "approved"
        if status is ApprovalStatus.PENDING:
            return "pending"
        return "denied"

    def _fail(
        self,
        cid: str,
        tool_name: str,
        exc: ToolError,
        *,
        actor: str,
        emit_kind: EventKind,
        ok: bool = False,
    ) -> ToolResult:
        result = ToolResult(
            ok=ok,
            error_code=exc.code,
            error_message=str(exc),
            cost_usd=Decimal("0.0000"),
        )
        payload: dict[str, Any] = {
            "tool": tool_name,
            "request_id": cid,
        }
        if emit_kind is EventKind.TOOL_DENIED:
            payload["reason"] = exc.code
        else:  # TOOL_RESULT
            payload["ok"] = ok
            payload["error"] = f"{exc.code}: {exc}"
        self._events.append(
            emit_kind, payload, actor=actor, correlation=cid
        )
        with self._cache_lock:
            self._completed[cid] = result
        return result

    def _emit_only(
        self,
        cid: str,
        tool_name: str,
        exc: ToolError,
        *,
        actor: str,
    ) -> ToolResult:
        """Emit a denied event but DO NOT cache (so retry is possible)."""
        self._events.append(
            EventKind.TOOL_DENIED,
            {
                "tool": tool_name,
                "request_id": cid,
                "reason": exc.code,
            },
            actor=actor,
            correlation=cid,
        )
        return ToolResult(
            ok=False,
            error_code=exc.code,
            error_message=str(exc),
            extra={"approval_request_id": getattr(exc, "request_id", cid)},
        )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = ["Tools"]
