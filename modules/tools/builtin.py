"""Pre-built tool definitions exposed to every agent.

Categories follow the PLAN: communication / workspace / memory / governance /
external. Each tool defines a pydantic ``Args`` and ``Out`` model so the
Tool Layer can validate inputs, render Claude SDK schemas, and serialise
results into the event log.

Executors here intentionally stay thin: they delegate to the underlying
modules (Identity, Memory, Workspace, Approvals, Connector). Future-module
behaviour (full inter-agent chat routing, full task board, etc.) is recorded
as audit events even when the consuming module isn't built yet.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    ApprovalStatus,
    RouteTarget,
)
from modules.cost import Money
from modules.event_store import EventKind
from modules.identity import Role
from modules.memory import Kind as MemoryKind
from modules.storage import PROJECT_WORKSPACE_ID

from .exceptions import (
    ArgsValidationError,
    ExecutorFailure,
    PendingApproval,
    ToolPermissionDenied,
)
from .models import (
    ApprovalRequirement,
    ToolCategory,
    ToolContext,
    ToolDef,
)

# --------------------------------------------------------------------- model

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------- communication --------------------------------------------------


class SendMessageArgs(_Strict):
    recipient_id: str = Field(min_length=1)
    body: str = Field(min_length=1, max_length=64_000)


class SendMessageOut(_Strict):
    message_id: str


async def _exec_send_message(
    ctx: ToolContext, args: SendMessageArgs,
) -> SendMessageOut:
    """Dispatch a chat message via the Orchestrator.

    The Orchestrator owns per-agent inbox queues, rate limiting, loop
    detection, and the ``MESSAGE_SENT`` audit emission. Without going
    through it the recipient never wakes up — which is the bug we hit when
    CEO talked to HR but HR never received anything.

    A non-``QUEUED`` result is reflected back via ``message_id`` so the
    LLM can react (rate limit, paused, invalid target, etc.).
    """
    msg_id = f"msg-{ctx.correlation_id[:12]}"
    orchestrator = getattr(ctx.services, "orchestrator", None)
    if orchestrator is None:
        # Defensive fallback: at least keep the audit trail truthful.
        ctx.services.events.append(
            EventKind.MESSAGE_SENT,
            {
                "from_agent": ctx.agent_id,
                "to_agent": args.recipient_id,
                "content": args.body,
            },
            actor=ctx.agent_id,
            correlation=ctx.correlation_id,
        )
        return SendMessageOut(message_id=f"{msg_id}:no-orchestrator")

    result = await orchestrator.send(
        from_agent=ctx.agent_id,
        to_agent=args.recipient_id,
        content=args.body,
        correlation_id=ctx.correlation_id,
    )
    rejection = getattr(result, "value", str(result))
    if rejection != "queued":
        return SendMessageOut(message_id=f"{msg_id}:rejected:{rejection}")
    return SendMessageOut(message_id=msg_id)


send_message = ToolDef(
    name="send_message",
    description="Send a chat message to another agent inside this company.",
    schema=SendMessageArgs,
    output_schema=SendMessageOut,
    category=ToolCategory.COMMUNICATION,
    roles_allowed="any",
    executor=_exec_send_message,
)


# ---------- workspace ------------------------------------------------------


class WriteWorkspaceArgs(_Strict):
    path: str = Field(min_length=1)
    content: str


class WriteWorkspaceOut(_Strict):
    bytes_written: int


def _exec_write_workspace(
    ctx: ToolContext, args: WriteWorkspaceArgs
) -> WriteWorkspaceOut:
    data = args.content.encode("utf-8")
    res = ctx.services.workspace.write(ctx.agent_id, args.path, data)
    return WriteWorkspaceOut(bytes_written=res.bytes_written)


write_my_workspace = ToolDef(
    name="write_my_workspace",
    description="Write a file inside the agent's own workspace.",
    schema=WriteWorkspaceArgs,
    output_schema=WriteWorkspaceOut,
    category=ToolCategory.WORKSPACE,
    roles_allowed="any",
    executor=_exec_write_workspace,
)


class ReadWorkspaceArgs(_Strict):
    path: str = Field(min_length=1)


class ReadWorkspaceOut(_Strict):
    content: str


def _exec_read_my_workspace(
    ctx: ToolContext, args: ReadWorkspaceArgs
) -> ReadWorkspaceOut:
    raw = ctx.services.workspace.read_own(ctx.agent_id, args.path)
    return ReadWorkspaceOut(content=raw.decode("utf-8", errors="replace"))


read_my_workspace = ToolDef(
    name="read_my_workspace",
    description="Read a file from the agent's own workspace.",
    schema=ReadWorkspaceArgs,
    output_schema=ReadWorkspaceOut,
    category=ToolCategory.WORKSPACE,
    roles_allowed="any",
    executor=_exec_read_my_workspace,
)


class ListWorkspaceArgs(_Strict):
    path: str = Field(default="")


class ListWorkspaceOut(_Strict):
    entries: list[dict[str, Any]] = Field(default_factory=list)


def _exec_list_my_workspace(
    ctx: ToolContext, args: ListWorkspaceArgs
) -> ListWorkspaceOut:
    raw = ctx.services.workspace.list(ctx.agent_id, args.path)
    return ListWorkspaceOut(
        entries=[
            {
                "relative_path": e.relative_path,
                "size": e.size,
                "is_dir": e.is_dir,
                "modified_at": e.modified_at,
            }
            for e in raw
        ]
    )


list_my_workspace = ToolDef(
    name="list_my_workspace",
    description="List files in the agent's own workspace.",
    schema=ListWorkspaceArgs,
    output_schema=ListWorkspaceOut,
    category=ToolCategory.WORKSPACE,
    roles_allowed="any",
    executor=_exec_list_my_workspace,
)


class ReadSubordinateArgs(_Strict):
    owner_id: str = Field(min_length=1)
    path: str = Field(min_length=1)


def _exec_read_subordinate(
    ctx: ToolContext, args: ReadSubordinateArgs
) -> ReadWorkspaceOut:
    raw = ctx.services.workspace.read_subordinate(
        ctx.agent_id, args.owner_id, args.path
    )
    return ReadWorkspaceOut(content=raw.decode("utf-8", errors="replace"))


read_subordinate_workspace = ToolDef(
    name="read_subordinate_workspace",
    description="Read a file in a subordinate's workspace (Identity-gated).",
    schema=ReadSubordinateArgs,
    output_schema=ReadWorkspaceOut,
    category=ToolCategory.WORKSPACE,
    roles_allowed="any",
    executor=_exec_read_subordinate,
)


def _exec_write_project_workspace(
    ctx: ToolContext, args: WriteWorkspaceArgs
) -> WriteWorkspaceOut:
    data = args.content.encode("utf-8")
    res = ctx.services.workspace.write(PROJECT_WORKSPACE_ID, args.path, data)
    return WriteWorkspaceOut(bytes_written=res.bytes_written)


write_project_workspace = ToolDef(
    name="write_project_workspace",
    description=(
        "Write a file into the company's shared project workspace. Use this "
        "for product code, designs, specs, and other artifacts teammates must share."
    ),
    schema=WriteWorkspaceArgs,
    output_schema=WriteWorkspaceOut,
    category=ToolCategory.WORKSPACE,
    roles_allowed="any",
    executor=_exec_write_project_workspace,
)


def _exec_read_project_workspace(
    ctx: ToolContext, args: ReadWorkspaceArgs
) -> ReadWorkspaceOut:
    raw = ctx.services.workspace.read_own(PROJECT_WORKSPACE_ID, args.path)
    return ReadWorkspaceOut(content=raw.decode("utf-8", errors="replace"))


read_project_workspace = ToolDef(
    name="read_project_workspace",
    description="Read a file from the company's shared project workspace.",
    schema=ReadWorkspaceArgs,
    output_schema=ReadWorkspaceOut,
    category=ToolCategory.WORKSPACE,
    roles_allowed="any",
    executor=_exec_read_project_workspace,
)


def _exec_list_project_workspace(
    ctx: ToolContext, args: ListWorkspaceArgs
) -> ListWorkspaceOut:
    raw = ctx.services.workspace.list(PROJECT_WORKSPACE_ID, args.path)
    return ListWorkspaceOut(
        entries=[
            {
                "relative_path": e.relative_path,
                "size": e.size,
                "is_dir": e.is_dir,
                "modified_at": e.modified_at,
            }
            for e in raw
        ]
    )


list_project_workspace = ToolDef(
    name="list_project_workspace",
    description="List files in the company's shared project workspace.",
    schema=ListWorkspaceArgs,
    output_schema=ListWorkspaceOut,
    category=ToolCategory.WORKSPACE,
    roles_allowed="any",
    executor=_exec_list_project_workspace,
)


# ---------- memory ---------------------------------------------------------


class RecallArgs(_Strict):
    query: str = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=50)


class RecallHitOut(_Strict):
    kind: str
    item_id: str
    content: str
    score: float


class RecallOut(_Strict):
    hits: list[RecallHitOut] = Field(default_factory=list)


def _exec_recall(ctx: ToolContext, args: RecallArgs) -> RecallOut:
    hits = ctx.services.memory.recall(ctx.agent_id, args.query, args.k)
    return RecallOut(
        hits=[
            RecallHitOut(
                kind=h.kind.value,
                item_id=h.item_id,
                content=h.content,
                score=h.score,
            )
            for h in hits
        ]
    )


recall_memory = ToolDef(
    name="recall_memory",
    description="Vector-recall semantic + episodic memory items.",
    schema=RecallArgs,
    output_schema=RecallOut,
    category=ToolCategory.MEMORY,
    roles_allowed="any",
    executor=_exec_recall,
)


class RememberFactArgs(_Strict):
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1)


class RememberOut(_Strict):
    memory_id: str


def _exec_remember_fact(
    ctx: ToolContext, args: RememberFactArgs
) -> RememberOut:
    mid = ctx.services.memory.remember_fact(ctx.agent_id, args.key, args.value)
    return RememberOut(memory_id=mid)


remember_fact = ToolDef(
    name="remember_fact",
    description="Store a typed key→value semantic fact.",
    schema=RememberFactArgs,
    output_schema=RememberOut,
    category=ToolCategory.MEMORY,
    roles_allowed="any",
    executor=_exec_remember_fact,
)


class SummariseArgs(_Strict):
    summary: str = Field(min_length=1, max_length=8_000)


def _exec_summarise(ctx: ToolContext, args: SummariseArgs) -> RememberOut:
    mid = ctx.services.memory.remember_episode(ctx.agent_id, args.summary)
    return RememberOut(memory_id=mid)


summarize_and_remember = ToolDef(
    name="summarize_and_remember",
    description="Summarise current task and store it as an episodic memory.",
    schema=SummariseArgs,
    output_schema=RememberOut,
    category=ToolCategory.MEMORY,
    roles_allowed="any",
    executor=_exec_summarise,
)


# ---------- governance -----------------------------------------------------


class ReportArgs(_Strict):
    body: str = Field(min_length=1, max_length=16_000)


class ReportOut(_Strict):
    delivered_to: str


def _check_only_ceo(ctx: ToolContext, _: BaseModel) -> None:
    if ctx.role is not Role.CEO:
        raise ToolPermissionDenied("only_ceo")


_REPORT_BOARD_LIFETIME_MAX = 8
_REPORT_BOARD_COOLDOWN_SECONDS = 300.0


def _exec_report_to_board(ctx: ToolContext, args: ReportArgs) -> ReportOut:
    # Rate-limit: in earlier runs the CEO produced 16+ board reports in
    # minutes (almost all of them complaining about "platform issues"
    # that were really self-inflicted message-loop rejections). Hard cap
    # plus a cooldown so the Board feed stays signal, not noise.
    from datetime import UTC, datetime

    recent = ctx.services.events.read(
        kinds=[EventKind.TOOL_CALLED],
        actor=ctx.agent_id,
        limit=500,
    )
    board_calls = [
        e for e in recent
        if (e.payload or {}).get("tool") == "report_to_board"
    ]
    if len(board_calls) >= _REPORT_BOARD_LIFETIME_MAX:
        raise ExecutorFailure(
            f"report_to_board_lifetime_cap:{_REPORT_BOARD_LIFETIME_MAX}; "
            "the Board has already been notified — solve the issue "
            "yourself"
        )
    if board_calls:
        last_ts = board_calls[-1].ts_real
        gap = (datetime.now(UTC) - last_ts).total_seconds()
        if gap < _REPORT_BOARD_COOLDOWN_SECONDS:
            wait = int(_REPORT_BOARD_COOLDOWN_SECONDS - gap)
            raise ExecutorFailure(
                f"report_to_board_cooldown:{wait}s; "
                "you just notified the Board — wait, then solve the "
                "issue yourself instead of repeating the report"
            )

    ctx.services.events.append(
        EventKind.MESSAGE_SENT,
        {
            "from_agent": ctx.agent_id,
            "to_agent": "BOARD",
            "content": args.body,
        },
        actor=ctx.agent_id,
        correlation=ctx.correlation_id,
    )
    return ReportOut(delivered_to="BOARD")


report_to_board = ToolDef(
    name="report_to_board",
    description=(
        "CEO escalation to the founder Board. RESERVED for: (a) major "
        "mission milestones (e.g. product launched, first customer), "
        "(b) governance decisions that require Board approval, "
        "(c) genuine ethical/legal escalations. NEVER use for status "
        "updates, platform issues, messaging-system complaints, "
        "infrastructure problems, or 'I am stuck' reports — solve those "
        "yourself. Board members are not on-call and cannot fix message "
        "delivery, loop rejections, or your own retries. Limited to a "
        "very small number of calls; abuse triggers rate limit."
    ),
    schema=ReportArgs,
    output_schema=ReportOut,
    category=ToolCategory.GOVERNANCE,
    roles_allowed=frozenset({Role.CEO}),
    permission_check=_check_only_ceo,
    executor=_exec_report_to_board,
)


class EscalateArgs(_Strict):
    body: str = Field(min_length=1, max_length=16_000)


def _exec_escalate(ctx: ToolContext, args: EscalateArgs) -> ReportOut:
    manager = ctx.services.identity.manager_of(ctx.agent_id)
    if manager is None:
        raise ToolPermissionDenied("no_manager")
    ctx.services.events.append(
        EventKind.MESSAGE_SENT,
        {
            "from_agent": ctx.agent_id,
            "to_agent": manager,
            "content": args.body,
        },
        actor=ctx.agent_id,
        correlation=ctx.correlation_id,
    )
    return ReportOut(delivered_to=manager)


escalate_to_manager = ToolDef(
    name="escalate_to_manager",
    description="Escalate an issue to the agent's direct manager.",
    schema=EscalateArgs,
    output_schema=ReportOut,
    category=ToolCategory.GOVERNANCE,
    roles_allowed="any",
    executor=_exec_escalate,
)


class ProposeHireArgs(_Strict):
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    role_title: str = Field(min_length=1, max_length=120)
    role_description: str = Field(min_length=1, max_length=8_000)
    reports_to: str = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=8_000)


class ProposeOut(_Strict):
    request_id: str | None = None
    status: str
    agent_id: str | None = None


async def _exec_propose_hire(
    ctx: ToolContext, args: ProposeHireArgs
) -> ProposeOut:
    payload = args.model_dump(mode="json", exclude_none=True)
    first_name = _clean_candidate_name(payload.get("first_name"))
    last_name = _clean_candidate_name(payload.get("last_name"))
    if first_name:
        payload["first_name"] = first_name
    else:
        payload.pop("first_name", None)
    if last_name:
        payload["last_name"] = last_name
    else:
        payload.pop("last_name", None)
    payload["role_description"] = _strip_reports_to_line(
        str(payload["role_description"])
    )
    hire_service = getattr(ctx.services, "hire_service", None)
    if hire_service is None:
        raise ExecutorFailure("hire_service_not_configured")
    result = hire_service.hire_member(
        requester_id=ctx.agent_id,
        payload=payload,
    )
    if hasattr(result, "__await__"):
        result = await result
    agent_id = None
    status = "hired"
    if isinstance(result, dict):
        agent_id = result.get("agent_id")
        status = str(result.get("status") or status)
    else:
        agent_id = getattr(result, "agent_id", None)
        status = str(getattr(result, "status", status))
    return ProposeOut(status=status, agent_id=agent_id)


propose_hire = ToolDef(
    name="propose_hire",
    description=(
        "Hire a new agent directly. Provide role_title, role_description, "
        "reports_to, and rationale. Candidate first/last names are optional; "
        "do not invent placeholders. A company can have at most 100 active agents. "
        "Keep the org tree balanced: the CEO may have at most 5 direct reports, "
        "and every other manager may have at most 4."
    ),
    schema=ProposeHireArgs,
    output_schema=ProposeOut,
    category=ToolCategory.GOVERNANCE,
    roles_allowed=frozenset({Role.HR}),
    executor=_exec_propose_hire,
)


_PLACEHOLDER_NAME_RE = re.compile(
    r"^(tbd|todo|unknown|new|candidate|hire|n/?a|none|null|product designer|engineer|designer)$",
    re.IGNORECASE,
)


def _clean_candidate_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return None
    if _PLACEHOLDER_NAME_RE.match(cleaned):
        return None
    if len(cleaned.split()) > 2:
        return None
    return cleaned


def _strip_reports_to_line(description: str) -> str:
    lines = []
    for line in description.splitlines():
        if line.strip().lower().startswith("reports to:"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


class ProposeFireArgs(_Strict):
    target_agent_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=8_000)


def _exec_propose_fire(
    ctx: ToolContext, args: ProposeFireArgs
) -> ProposeOut:
    if ctx.services.performance is not None:
        outcome = ctx.services.performance.propose_fire(
            caller_id=ctx.agent_id,
            target_id=args.target_agent_id,
            reason=args.reason,
        )
        return ProposeOut(
            request_id=outcome.request_id or "",
            status=outcome.result,
        )
    outcome = ctx.services.identity.fire(
        actor_id=ctx.agent_id,
        target_id=args.target_agent_id,
        reason=args.reason,
        via_hr=True,
    )
    return ProposeOut(
        request_id=outcome.request_id or "",
        status=outcome.result.value,
    )


propose_fire = ToolDef(
    name="propose_fire",
    description=(
        "Fire a subordinate directly. No board approval is required, but a "
        "specific business reason is mandatory; test/no-reason fires are denied."
    ),
    schema=ProposeFireArgs,
    output_schema=ProposeOut,
    category=ToolCategory.GOVERNANCE,
    roles_allowed="any",
    executor=_exec_propose_fire,
)


class GiveFeedbackArgs(_Strict):
    target_id: str = Field(min_length=1)
    rating: int = Field(ge=1, le=5)
    note: str = Field(min_length=1, max_length=8_000)


class GiveFeedbackOut(_Strict):
    feedback_id: str


def _exec_give_feedback(
    ctx: ToolContext, args: GiveFeedbackArgs,
) -> GiveFeedbackOut:
    if ctx.services.performance is None:
        raise ExecutorFailure("performance_not_configured")
    feedback = ctx.services.performance.give_feedback(
        caller_id=ctx.agent_id,
        target_id=args.target_id,
        rating=args.rating,
        note=args.note,
    )
    return GiveFeedbackOut(feedback_id=feedback.id)


give_feedback = ToolDef(
    name="give_feedback",
    description="Give performance feedback to a direct report. CEO may give feedback to anyone.",
    schema=GiveFeedbackArgs,
    output_schema=GiveFeedbackOut,
    category=ToolCategory.GOVERNANCE,
    roles_allowed="any",
    executor=_exec_give_feedback,
)


class ProposeRoleChangeArgs(_Strict):
    target_id: str = Field(min_length=1)
    new_role_title: str = Field(min_length=1, max_length=120)
    new_role_description: str = Field(min_length=1, max_length=8_000)
    rationale: str = Field(min_length=1, max_length=8_000)


def _exec_propose_role_change(
    ctx: ToolContext, args: ProposeRoleChangeArgs,
) -> ProposeOut:
    if ctx.services.performance is None:
        raise ExecutorFailure("performance_not_configured")
    proposal = ctx.services.performance.propose_role_change(
        caller_id=ctx.agent_id,
        target_id=args.target_id,
        new_role_title=args.new_role_title,
        new_role_description=args.new_role_description,
        rationale=args.rationale,
    )
    return ProposeOut(
        request_id=proposal.request_id,
        status=proposal.status,
    )


propose_role_change = ToolDef(
    name="propose_role_change",
    description="Apply a role title/description change for a direct report. No board approval is required.",
    schema=ProposeRoleChangeArgs,
    output_schema=ProposeOut,
    category=ToolCategory.GOVERNANCE,
    roles_allowed="any",
    executor=_exec_propose_role_change,
)


class ProposeReassignArgs(_Strict):
    target_id: str = Field(min_length=1)
    new_reports_to: str = Field(min_length=1)
    reason: str = Field(min_length=8, max_length=8_000)


def _exec_propose_reassign(
    ctx: ToolContext, args: ProposeReassignArgs,
) -> ProposeOut:
    """Move a subordinate under a different manager.

    Caller must be the CEO or the target's current manager (enforced
    in the org layer). The destination manager must have capacity and
    must not create a cycle.
    """
    ctx.services.identity.reassign(
        actor_id=ctx.agent_id,
        target_id=args.target_id,
        new_reports_to=args.new_reports_to,
        reason=args.reason,
    )
    return ProposeOut(
        agent_id=args.target_id,
        status="reassigned",
    )


propose_reassign = ToolDef(
    name="propose_reassign",
    description=(
        "Move an existing direct report under a different manager. Use this "
        "to rebalance the org chart — for example after hiring a new "
        "Engineering Manager, move existing engineers from the CEO under the "
        "new manager. Only the target's current manager or the CEO may call "
        "this. The new manager must have capacity (CEO ≤5, others ≤4) and "
        "the move must not create a cycle. Provide a specific reason; "
        "rejected if the new manager is HR and the target is not HR, or if "
        "the target is one of the special bootstrap roles (CEO/HR/Security)."
    ),
    schema=ProposeReassignArgs,
    output_schema=ProposeOut,
    category=ToolCategory.GOVERNANCE,
    roles_allowed="any",
    executor=_exec_propose_reassign,
)


class RequestApprovalArgs(_Strict):
    kind: ApprovalKind
    payload: dict[str, Any] = Field(default_factory=dict)


def _exec_request_approval(
    ctx: ToolContext, args: RequestApprovalArgs
) -> ProposeOut:
    if args.kind in (
        ApprovalKind.HIRE,
        ApprovalKind.ROLE_CHANGE,
        ApprovalKind.FIRE_DEPTH_1,
    ):
        raise ToolPermissionDenied(
            f"approval_not_required:{args.kind.value}; "
            "use propose_hire, propose_role_change, or propose_fire"
        )
    approval = ctx.services.approvals.request(
        kind=args.kind,
        requester_id=ctx.agent_id,
        payload=args.payload,
        route=ApprovalRoute(target=RouteTarget.BOARD),
        request_id=f"req-{ctx.correlation_id}",
    )
    return ProposeOut(
        request_id=approval.request_id, status=approval.status.value
    )


request_approval = ToolDef(
    name="request_approval",
    description=(
        "Open an approval request routed to the board for exceptional "
        "governance actions. Do not use for hire or role changes; those are "
        "direct actions via propose_hire and propose_role_change."
    ),
    schema=RequestApprovalArgs,
    output_schema=ProposeOut,
    category=ToolCategory.GOVERNANCE,
    roles_allowed="any",
    executor=_exec_request_approval,
)


class OrgChartArgs(_Strict):
    pass


class OrgChartOut(_Strict):
    self_id: str
    role: str
    manager_id: str | None
    direct_reports: list[str] = Field(default_factory=list)
    role_title: str | None = None
    first_name: str = ""
    last_name: str = ""


def _exec_org_chart(ctx: ToolContext, _: OrgChartArgs) -> OrgChartOut:
    me = ctx.services.identity.get(ctx.agent_id)
    return OrgChartOut(
        self_id=me.id,
        role=me.role.value,
        manager_id=ctx.services.identity.manager_of(ctx.agent_id),
        direct_reports=ctx.services.identity.direct_reports(ctx.agent_id),
        role_title=me.role_title,
        first_name=me.first_name,
        last_name=me.last_name,
    )


query_org_chart = ToolDef(
    name="query_org_chart",
    description="Read-only view of the agent's place in the org chart.",
    schema=OrgChartArgs,
    output_schema=OrgChartOut,
    category=ToolCategory.INTERNAL_DATA,
    roles_allowed="any",
    executor=_exec_org_chart,
)


class EmptyArgs(_Strict):
    pass


class ListOut(_Strict):
    items: list[dict[str, Any]] = Field(default_factory=list)


def _exec_list_my_approvals(ctx: ToolContext, _: EmptyArgs) -> ListOut:
    # Approvals service doesn't (yet) expose a per-requester index; this is a
    # placeholder that returns an empty list. Recorded so tests can verify
    # the tool wiring without needing the future query API.
    return ListOut(items=[])


list_my_approvals = ToolDef(
    name="list_my_approvals",
    description="List approvals the agent has requested or is owed.",
    schema=EmptyArgs,
    output_schema=ListOut,
    category=ToolCategory.INTERNAL_DATA,
    roles_allowed="any",
    executor=_exec_list_my_approvals,
)


def _exec_list_my_tasks(ctx: ToolContext, _: EmptyArgs) -> ListOut:
    return ListOut(items=[])


list_my_tasks = ToolDef(
    name="list_my_tasks",
    description="List tasks assigned to the agent on the task board.",
    schema=EmptyArgs,
    output_schema=ListOut,
    category=ToolCategory.INTERNAL_DATA,
    roles_allowed="any",
    executor=_exec_list_my_tasks,
)


# ---------- external -------------------------------------------------------


class ExternalCallArgs(_Strict):
    service: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    estimated_cost_usd: str = Field(default="0.00")


class ExternalCallOut(_Strict):
    response: dict[str, Any] = Field(default_factory=dict)


def _estimate_external(args: BaseModel) -> Money:
    assert isinstance(args, ExternalCallArgs)
    try:
        return Money.of(args.estimated_cost_usd)
    except Exception as exc:
        raise ArgsValidationError(f"bad estimated_cost_usd: {exc}") from exc


async def _exec_external_call(
    ctx: ToolContext, args: ExternalCallArgs
) -> ExternalCallOut:
    if ctx.services.connector is None:
        raise ExecutorFailure("no_connector_configured")
    response = await ctx.services.connector.call(
        args.service, args.endpoint, args.payload
    )
    if isinstance(response, BaseModel):
        body = response.model_dump(mode="json")
    elif isinstance(response, dict):
        body = response
    else:
        body = {"value": response}
    ctx.services.events.append(
        EventKind.EXTERNAL_CALL,
        {
            "service": args.service,
            "endpoint": args.endpoint,
            "request_id": ctx.correlation_id,
            "status": "ok",
        },
        actor=ctx.agent_id,
        correlation=ctx.correlation_id,
    )
    return ExternalCallOut(response=body)


external_call = ToolDef(
    name="external_call",
    description="Call an external service through the connector.",
    schema=ExternalCallArgs,
    output_schema=ExternalCallOut,
    category=ToolCategory.EXTERNAL,
    roles_allowed="any",
    requires_approval=ApprovalRequirement.EXTERNAL,
    cost_estimator=_estimate_external,
    executor=_exec_external_call,
)


class PayInvoiceArgs(_Strict):
    vendor: str = Field(min_length=1)
    amount_usd: str = Field(min_length=1)
    description: str = Field(default="", max_length=2_000)


class PayInvoiceOut(_Strict):
    vendor: str
    paid_usd: str


def _estimate_invoice(args: BaseModel) -> Money:
    assert isinstance(args, PayInvoiceArgs)
    try:
        return Money.of(args.amount_usd)
    except Exception as exc:
        raise ArgsValidationError(f"bad amount_usd: {exc}") from exc


def _exec_pay_invoice(
    ctx: ToolContext, args: PayInvoiceArgs
) -> PayInvoiceOut:
    return PayInvoiceOut(vendor=args.vendor, paid_usd=args.amount_usd)


pay_invoice = ToolDef(
    name="pay_invoice",
    description="Pay an external invoice (gated by EXPENSE approval).",
    schema=PayInvoiceArgs,
    output_schema=PayInvoiceOut,
    category=ToolCategory.EXTERNAL,
    roles_allowed=frozenset({Role.CEO, Role.MEMBER}),
    requires_approval=ApprovalRequirement.EXPENSE,
    cost_estimator=_estimate_invoice,
    executor=_exec_pay_invoice,
)


class RegisterSubArgs(_Strict):
    description: str = Field(min_length=1)
    amount_usd: str = Field(min_length=1)
    period_days: int = Field(default=30, ge=1, le=365)


class RegisterSubOut(_Strict):
    description: str
    amount_usd: str
    period_days: int


def _estimate_sub(args: BaseModel) -> Money:
    assert isinstance(args, RegisterSubArgs)
    try:
        return Money.of(args.amount_usd)
    except Exception as exc:
        raise ArgsValidationError(f"bad amount_usd: {exc}") from exc


def _exec_register_subscription(
    ctx: ToolContext, args: RegisterSubArgs
) -> RegisterSubOut:
    return RegisterSubOut(
        description=args.description,
        amount_usd=args.amount_usd,
        period_days=args.period_days,
    )


register_subscription = ToolDef(
    name="register_subscription",
    description="Register a recurring subscription (EXPENSE-gated).",
    schema=RegisterSubArgs,
    output_schema=RegisterSubOut,
    category=ToolCategory.EXTERNAL,
    roles_allowed=frozenset({Role.CEO, Role.MEMBER}),
    requires_approval=ApprovalRequirement.EXPENSE,
    cost_estimator=_estimate_sub,
    executor=_exec_register_subscription,
)


# --------------------------------------------------------------------- registry


BUILTIN_TOOLS: tuple[ToolDef, ...] = (
    send_message,
    request_approval,
    read_my_workspace,
    write_my_workspace,
    list_my_workspace,
    read_subordinate_workspace,
    read_project_workspace,
    write_project_workspace,
    list_project_workspace,
    recall_memory,
    remember_fact,
    summarize_and_remember,
    report_to_board,
    escalate_to_manager,
    propose_hire,
    propose_fire,
    give_feedback,
    propose_role_change,
    propose_reassign,
    external_call,
    pay_invoice,
    register_subscription,
    query_org_chart,
    list_my_approvals,
    list_my_tasks,
)


def register_builtins(tools: Any) -> None:
    """Register every built-in :class:`ToolDef` on ``tools``."""
    for t in BUILTIN_TOOLS:
        tools.register(t)


# Used elsewhere only for the `_` references; silence "unused" by re-exporting.
_KIND = MemoryKind
_PENDING = PendingApproval
_STATUS = ApprovalStatus
