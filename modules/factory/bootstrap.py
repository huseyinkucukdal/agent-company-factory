"""End-to-end bootstrap: build a fully-wired :class:`CompanyHandle` from a
:class:`CompanySpec`.

This module is the heart of the factory. It is structured as a sequence
of pure helpers that return the next service so a partial failure can be
detected at any line and rolled back by the caller.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from modules.agent_runtime import (
    Agent,
    AgentDeps,
    LLMClient,
    MessageKind,
    PersonaLoader,
    default_persona_loader,
)
from modules.approvals import Approval, ApprovalKind, ApprovalStatus, Approvals
from modules.clock import Clock
from modules.connector import Allowlist, Connector, in_memory_secrets
from modules.cost import (
    Budget,
    Pricing,
    Subscriptions,
)
from modules.cost import (
    migrate as cost_migrate,
)
from modules.efficiency import EfficiencyService, FindingStore
from modules.event_store import EventKind, EventStore
from modules.identity import Agent as IdAgent
from modules.identity import Org, Role, Status
from modules.llm.counter import LLMRequestCounter
from modules.memory import HashEmbedder, Memory
from modules.orchestrator import (
    InMemoryMessageQueue,
    Orchestrator,
    RateLimitConfig,
)
from modules.performance import Performance
from modules.security_agent import SecurityDeps, SecurityPolicy
from modules.storage import (
    CompanyDB,
    CompanyQuota,
    PROJECT_WORKSPACE_ID,
    Quota,
    Workspace,
)
from modules.tools import Tools, register_builtins

from .adapters import (
    EventStoreEventSink,
    FireApprovalAdapter,
    MemoryIdentityAdapter,
)
from .handle import CompanyHandle
from .spec import CompanySpec

_log = logging.getLogger(__name__)


class _OrchestratorProbe:
    """Forwards `is_company_idle` to a (later-installed) Orchestrator.

    Lets us satisfy Clock's :class:`OrchestratorIdleProbe` Protocol without
    introducing a circular construction dependency between Clock and
    Orchestrator.
    """

    orchestrator: Orchestrator | None = None

    def is_company_idle(self) -> bool:
        return self.orchestrator is None or self.orchestrator.is_company_idle()


class _RuntimeHireService:
    def __init__(
        self,
        handle_ref: list[CompanyHandle | None],
        config: FactoryConfig,
    ) -> None:
        self._handle_ref = handle_ref
        self._config = config

    async def hire_member(
        self,
        *,
        requester_id: str,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        handle = self._handle_ref[0]
        if handle is None:
            raise RuntimeError("company_handle_not_ready")
        record = await _hire_member_from_payload(
            payload=payload,
            requester_id=requester_id,
            handle=handle,
            config=self._config,
            notification_prefix="Hire completed.",
        )
        _cancel_duplicate_hire_approvals(
            handle,
            current_request_id=None,
            role_title=payload.get("role_title"),
            reports_to=payload.get("reports_to"),
            by=requester_id,
        )
        return {"agent_id": record.id, "status": "hired"}


LLMFactory = Callable[[str, str], LLMClient]
"""Build the LLM client for a (company_id, agent_id). Tests inject fakes."""

PostCreateHook = Callable[[CompanyHandle], None]
"""Optional hook invoked after a company is fully bootstrapped.

Used by :mod:`modules.integration` to register cross-cutting connector
services (e.g. inter-company messaging) without leaking those
dependencies into the factory itself.
"""


@dataclass
class FactoryConfig:
    """Cross-company knobs used by the factory at bootstrap time.

    `llm_factory` is required — there is no production fallback because
    the choice of model/provider is deployment-specific.

    `post_create` runs after both fresh creates and restarts. Failures
    are logged but do not roll the company back — it is purely additive
    wiring.
    """

    llm_factory: LLMFactory
    persona_loader: PersonaLoader | None = None
    rate_limit: RateLimitConfig | None = None
    post_create: PostCreateHook | None = None


def _persona_loader(cfg: FactoryConfig) -> PersonaLoader:
    return cfg.persona_loader or default_persona_loader()


def _resolve_role_to_id(
    org: Org, role: Role, fallback_ceo_id: str,
) -> str:
    """Look up the active agent with `role`. Falls back to CEO when none
    is found — keeps the bootstrap robust when extra agents reference the
    CEO role implicitly.
    """
    for a in org.all(status=Status.ACTIVE):
        if a.role is role:
            return a.id
    return fallback_ceo_id


def _wire_pricing() -> Pricing:
    """Default pricing catalogue. Tests may override later."""
    return Pricing()


def _set_budget(budget: Budget, spec: CompanySpec) -> None:
    budget.set_total(Decimal(str(spec.initial_budget_usd)))


def _ensure_project_workspace(workspace: Workspace, spec: CompanySpec) -> None:
    workspace.ensure_for_agent(
        PROJECT_WORKSPACE_ID,
        quota_mb=spec.company_disk_quota_mb,
    )


async def _send_welcome(
    orchestrator: Orchestrator, ceo_id: str, hr_id: str, sec_id: str,
    spec: CompanySpec,
) -> None:
    content = (
        f"Company created. Mission: {spec.mission}\n"
        f"Budget: ${Decimal(str(spec.initial_budget_usd))}\n"
        f"Your team: HR ({hr_id}), Security ({sec_id}). "
        f"Define your initial goals and make a plan."
    )
    await orchestrator.system_send(
        ceo_id, content, kind=MessageKind.USER_REQUEST,
    )


async def _hire_member_from_payload(
    *,
    payload: dict[str, Any],
    requester_id: str,
    handle: CompanyHandle,
    config: FactoryConfig,
    notification_prefix: str,
) -> IdAgent:
    """Create and start a live member agent from a hire payload."""
    p = payload
    first_name: str = p.get("first_name", "")
    last_name: str = p.get("last_name", "")
    role_title = str(p.get("role_title") or "").strip()
    role_description = str(p.get("role_description") or "").strip()
    reports_to = str(p.get("reports_to") or "").strip()
    if not role_title or not role_description or not reports_to:
        raise ValueError("invalid_hire_payload")
    first_name = _clean_hire_name(first_name)
    last_name = _clean_hire_name(last_name)
    if not first_name or not last_name:
        first_name, last_name = _fallback_member_name(handle)

    record = handle.identity.add_agent(
        role=Role.MEMBER,
        persona_ref="default",
        reports_to=reports_to,
        requested_by=requester_id,
        via_hr=True,
        first_name=first_name,
        last_name=last_name,
        role_title=role_title,
        role_description=role_description,
    )

    handle.workspace.create_for_agent(record.id, quota_mb=handle.default_agent_quota_mb)

    persona_loader = _persona_loader(config)
    llm_client = config.llm_factory(handle.company_id, record.id)
    deps = AgentDeps(
        identity=handle.identity,
        memory=handle.memory,
        tools=handle.tools,
        events=handle.events,
        cost=handle.budget,
        clock=handle.clock,
        llm=llm_client,
        persona_loader=persona_loader,
        company_id=handle.company_id,
        company_mission=handle.company_mission,
    )
    agent = Agent(record.id, deps)
    handle.agents[record.id] = agent
    handle.orchestrator.register(agent)
    await agent.start()

    display_name = f"{first_name} {last_name}".strip() or record.id
    title_str = f" ({role_title})" if role_title else ""
    short_role_desc = (role_description or "").strip()
    if len(short_role_desc) > 500:
        short_role_desc = short_role_desc[:497] + "…"

    # 1. Wake the new hire up with their own context. Without this they
    #    sit idle until their manager remembers to message them or an
    #    idle nudge fires — both unreliable.
    await handle.orchestrator.system_send(
        record.id,
        (
            f"You have been hired as {role_title}. You report to "
            f"{reports_to or '—'}. Your role description:\n"
            f"{short_role_desc or '(no description)'}\n\n"
            "Your manager will send your first concrete task shortly. "
            "If a few minutes pass with no message, introduce yourself "
            "to your manager and ask for the first deliverable."
        ),
        kind=MessageKind.NOTIFY,
    )

    # 2. Push the reporting manager to assign the first task right now.
    #    The hire is useless until they have something concrete to do,
    #    and previous runs showed managers happily moving on to the
    #    next hire instead of starting work.
    manager_recipient = reports_to or requester_id
    await handle.orchestrator.system_send(
        manager_recipient,
        (
            f"{display_name}{title_str} just joined your team "
            f"(id: {record.id}). Send them their first concrete "
            "deliverable NOW via send_message. Derive a specific 1–2 "
            "sentence task from this role description: "
            f"{short_role_desc or '(no description)'}"
        ),
        kind=MessageKind.NOTIFY,
    )

    # 3. Inform the hiring requester (typically HR) — but only if they
    #    are not already the reporting manager (avoid double notify).
    if requester_id and requester_id != manager_recipient:
        await handle.orchestrator.system_send(
            requester_id,
            (
                f"{notification_prefix} {display_name}{title_str} is "
                f"active (id: {record.id}, reports to "
                f"{reports_to or '—'}). The reporting manager has been "
                "asked to send the first task — your part is done."
            ),
            kind=MessageKind.NOTIFY,
        )
    return record


async def _spawn_hired_agent(approval: Approval, handle: CompanyHandle, config: FactoryConfig) -> None:
    """Create a live agent for a legacy approved HIRE request.

    Called as an asyncio Task so it runs inside the event loop and can
    await orchestrator operations.
    """
    try:
        await _hire_member_from_payload(
            payload=dict(approval.payload),
            requester_id=approval.requester_id,
            handle=handle,
            config=config,
            notification_prefix="Hire approved and completed.",
        )
    except Exception:  # noqa: BLE001
        _log.exception(
            "hire_spawn_failed",
            extra={"approval_id": approval.request_id},
        )
        with contextlib.suppress(Exception):
            await handle.orchestrator.system_send(
                approval.requester_id,
                (
                    "Legacy hire approval ignored because its payload is invalid. "
                    "Use propose_hire with role_title, role_description, and reports_to."
                ),
                kind=MessageKind.NOTIFY,
            )
        return
    role_title = approval.payload.get("role_title")
    reports_to = approval.payload.get("reports_to")
    requester_id = approval.requester_id
    _cancel_duplicate_hire_approvals(
        handle,
        current_request_id=approval.request_id,
        role_title=role_title,
        reports_to=reports_to,
        by=requester_id,
    )


_FALLBACK_MEMBER_NAMES: tuple[tuple[str, str], ...] = (
    ("Huseyin", "Kucukdal"),
    ("Ulara", "Kucukdal"),
    ("Luna", "Kucukdal"),
    ("Taylor", "Brooks"),
    ("Jamie", "Blake"),
    ("Quinn", "Hayes"),
    ("Sage", "Carter"),
    ("Rowan", "Ellis"),
)

_PLACEHOLDER_HIRE_NAMES = {
    "tbd",
    "todo",
    "unknown",
    "new",
    "candidate",
    "hire",
    "n/a",
    "na",
    "none",
    "null",
    "product designer",
    "engineer",
    "designer",
}


def _clean_hire_name(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    if not cleaned:
        return ""
    if cleaned.lower() in _PLACEHOLDER_HIRE_NAMES:
        return ""
    if len(cleaned.split()) > 2:
        return ""
    return cleaned


def _fallback_member_name(handle: CompanyHandle) -> tuple[str, str]:
    existing = {
        (a.first_name.lower(), a.last_name.lower())
        for a in handle.identity.all()
        if a.first_name or a.last_name
    }
    offset = len(existing)
    for idx in range(len(_FALLBACK_MEMBER_NAMES)):
        candidate = _FALLBACK_MEMBER_NAMES[(offset + idx) % len(_FALLBACK_MEMBER_NAMES)]
        if (candidate[0].lower(), candidate[1].lower()) not in existing:
            return candidate
    return ("Member", str(offset + 1))


def _cancel_duplicate_hire_approvals(
    handle: CompanyHandle,
    *,
    current_request_id: str | None,
    role_title: str | None,
    reports_to: str | None,
    by: str,
) -> None:
    if not role_title or not reports_to:
        return
    if current_request_id is None:
        rows = handle.db.connect().execute(
            "SELECT request_id, payload_json FROM approvals "
            "WHERE kind = 'hire' AND status = 'pending'",
        ).fetchall()
    else:
        rows = handle.db.connect().execute(
            "SELECT request_id, payload_json FROM approvals "
            "WHERE kind = 'hire' AND status = 'pending' AND request_id != ?",
            (current_request_id,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        same_role = str(payload.get("role_title", "")).strip().lower() == role_title.strip().lower()
        same_manager = payload.get("reports_to") == reports_to
        if not (same_role and same_manager):
            continue
        with contextlib.suppress(Exception):
            handle.approvals.cancel(
                row["request_id"],
                by=by,
                reason=(
                    f"duplicate_hire:{current_request_id}"
                    if current_request_id
                    else "duplicate_hire:direct_hire"
                ),
            )


def _cancel_pending_direct_governance_approvals(
    handle: CompanyHandle,
    *,
    by: str,
) -> None:
    rows = handle.db.connect().execute(
        "SELECT request_id FROM approvals "
        "WHERE kind IN ('hire', 'role_change', 'fire_depth_1') "
        "AND status = 'pending'"
    ).fetchall()
    for row in rows:
        with contextlib.suppress(Exception):
            handle.approvals.cancel(
                row["request_id"],
                by=by,
                reason="board_approval_no_longer_required",
            )


def _repair_unmanaged_members_to_ceo(
    handle: CompanyHandle,
    *,
    by: str,
) -> None:
    ceo_id = handle.bootstrap_agent_ids.get("ceo")
    if not ceo_id:
        return
    rows = handle.db.connect().execute(
        "SELECT id, first_name, last_name, role_title FROM agents "
        "WHERE role = ? AND status = ? AND reports_to IS NULL",
        (Role.MEMBER.value, Status.ACTIVE.value),
    ).fetchall()
    if not rows:
        return
    with handle.db.transaction() as conn:
        for row in rows:
            conn.execute(
                "UPDATE agents SET reports_to = ? WHERE id = ?",
                (ceo_id, row["id"]),
            )
    for row in rows:
        display = " ".join(
            part for part in (row["first_name"], row["last_name"]) if part
        ) or row["id"]
        title = f" ({row['role_title']})" if row["role_title"] else ""
        handle.events.append(
            EventKind.MESSAGE_SENT,
            {
                "from_agent": "system",
                "to_agent": ceo_id,
                "content": (
                    f"Repaired unmanaged agent {display}{title}. "
                    f"Agent ID {row['id']} now reports to the CEO."
                ),
            },
            actor=by,
        )


def _make_hire_completion_handler(
    handle_ref: list[CompanyHandle | None],
    config: FactoryConfig,
) -> Callable[[Approval], None]:
    """Return a subscriber callback that spawns an agent when a HIRE is approved.

    ``handle_ref`` is a single-element list whose slot is filled *after*
    ``build_company`` finishes constructing the handle — this breaks the
    circular dependency between the handler and the handle.
    """
    def _on_approval(approval: Approval) -> None:
        if approval.kind is not ApprovalKind.HIRE:
            return
        if approval.status is not ApprovalStatus.APPROVED:
            return
        handle = handle_ref[0]
        if handle is None:
            _log.warning("hire_completion_handler: handle not yet available")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _log.warning("hire_completion_handler: no running event loop")
            return
        loop.create_task(
            _spawn_hired_agent(approval, handle, config),
            name=f"hire-spawn:{approval.request_id}",
        )

    return _on_approval


# ---------------------------------------------------------------- entry


async def build_company(
    *,
    company_id: str,
    spec: CompanySpec,
    root: Path,
    requested_by: str,
    config: FactoryConfig,
    send_welcome: bool = True,
    fresh: bool = True,
) -> CompanyHandle:
    """Wire a fully-running company. Caller owns rollback on failure.

    `fresh=True` is for create_company; `fresh=False` is for
    restart_company and skips the bootstrap-agent step + welcome.
    """
    db = CompanyDB.init(company_id, root)
    db.migrate()
    cost_migrate(db)

    events = EventStore(db)
    events.migrate()

    approvals = Approvals(db, events)
    approvals.migrate()

    fire_adapter = FireApprovalAdapter(approvals)
    org = Org(db, events, fire_adapter)
    org.migrate()

    performance = Performance(db, events, org, approvals)
    performance.migrate()

    quota = Quota(db)
    quota.set_limit(CompanyQuota(), mb=spec.company_disk_quota_mb)

    workspace = Workspace(db, org, EventStoreEventSink(events))
    _ensure_project_workspace(workspace, spec)

    pricing = _wire_pricing()
    budget = Budget(db, events)
    if fresh:
        _set_budget(budget, spec)
    subscriptions = Subscriptions(db, budget, events)

    memory = Memory(db, MemoryIdentityAdapter(org), HashEmbedder())
    memory.migrate()

    llm_counter = LLMRequestCounter(db)
    llm_counter.migrate()

    secrets = in_memory_secrets()
    connector = Connector(
        cost=budget,
        approvals=approvals,
        events=events,
        secrets=secrets,
        allowlist=Allowlist(),
    )

    handle_ref: list[CompanyHandle | None] = [None]
    runtime_hire_service = _RuntimeHireService(handle_ref, config)
    tools = Tools(
        identity=org,
        cost=cast(Any, budget),
        approvals=cast(Any, approvals),
        workspace=cast(Any, workspace),
        memory=cast(Any, memory),
        events=cast(Any, events),
        connector=cast(Any, connector),
        performance=performance,
    )
    register_builtins(tools)
    tools.set_hire_service(runtime_hire_service)

    if fresh:
        ceo, hr, sec = _bootstrap_core_agents(org, requested_by)
        bootstrap_ids = {"ceo": ceo.id, "hr": hr.id, "security": sec.id}
        for a in (ceo, hr, sec):
            workspace.create_for_agent(
                a.id, quota_mb=spec.default_agent_quota_mb,
            )
        for ex in spec.extra_agents:
            mgr_id = _resolve_role_to_id(org, ex.reports_to_role, ceo.id)
            extra = org.add_agent(
                role=Role.MEMBER, persona_ref=ex.persona_ref,
                reports_to=mgr_id, requested_by=hr.id, via_hr=True,
                first_name=ex.first_name, last_name=ex.last_name,
                role_title=ex.role_title, role_description=ex.role_description,
            )
            workspace.create_for_agent(
                extra.id, quota_mb=spec.default_agent_quota_mb,
            )
    else:
        bootstrap_ids = _existing_core_ids(org)

    # Mutually-recursive deps: Clock needs an idle probe from the
    # Orchestrator and the Orchestrator needs the Clock. We construct a
    # tiny forwarder, build the clock, then build the orchestrator and
    # link the forwarder to it.
    queue = InMemoryMessageQueue()
    probe = _OrchestratorProbe()

    clock = Clock(db, events, probe, rate=spec.clock_rate)
    clock.migrate()

    orchestrator = Orchestrator(
        company_id,
        identity=org,
        events=events,
        clock=clock,
        queue=queue,
        rate_limit=config.rate_limit,
    )
    probe.orchestrator = orchestrator
    # Late-bind the orchestrator into the Tools layer so ``send_message``
    # can dispatch to recipient inboxes instead of only logging an event.
    tools.set_orchestrator(orchestrator)

    persona_loader = _persona_loader(config)
    agents: dict[str, Agent] = {}

    def _record_llm_request(
        *, ok: bool, rate_limited: bool = False,
        provider: str | None = None, model: str | None = None,
    ) -> None:
        llm_counter.increment(
            ok=ok, rate_limited=rate_limited,
            provider=provider, model=model,
        )

    for record in org.all(status=Status.ACTIVE):
        llm_client = config.llm_factory(company_id, record.id)
        # Duck-typed: the OpenAI/GitHub/Anthropic clients all carry an
        # ``_on_request`` slot for per-call counter callbacks.
        try:
            llm_client._on_request = _record_llm_request  # type: ignore[attr-defined]
        except AttributeError:
            pass
        deps = AgentDeps(
            identity=org,
            memory=memory,
            tools=tools,
            events=events,
            cost=budget,
            clock=clock,
            llm=llm_client,
            persona_loader=persona_loader,
            company_id=company_id,
            company_mission=spec.mission,
        )
        agent = Agent(record.id, deps)
        agents[record.id] = agent
        orchestrator.register(agent)

    sec_id = bootstrap_ids["security"]
    security_policy = SecurityPolicy(
        sec_id,
        SecurityDeps(
            approvals=approvals,
            events=events,
            orchestrator=orchestrator,
        ),
    )

    await orchestrator.start()
    for agent in agents.values():
        await agent.start()
    await clock.start()

    # Efficiency service is best-effort; failures here must not block company startup.
    efficiency: EfficiencyService | None = None
    try:
        finding_store = FindingStore(db)
        finding_store.migrate()
        efficiency = EfficiencyService(
            company_id=company_id,
            db=db,
            events=events,
            store=finding_store,
            approvals=approvals,
            org=org,
        )
        await efficiency.start()
    except Exception:  # noqa: BLE001
        _log.exception("efficiency service failed to start; continuing")
        efficiency = None

    if fresh and send_welcome:
        events.append(
            EventKind.COMPANY_CREATED,
            {"company_id": company_id, "name": spec.name},
            actor=requested_by,
        )
        await _send_welcome(
            orchestrator,
            bootstrap_ids["ceo"], bootstrap_ids["hr"], bootstrap_ids["security"],
            spec,
        )

    handle = CompanyHandle(
        company_id=company_id,
        db=db,
        workspace=workspace,
        quota=quota,
        events=events,
        clock=clock,
        identity=org,
        pricing=pricing,
        budget=budget,
        subscriptions=subscriptions,
        approvals=approvals,
        memory=memory,
        tools=tools,
        connector=connector,
        orchestrator=orchestrator,
        agents=agents,
        security_policy=security_policy,
        bootstrap_agent_ids=bootstrap_ids,
        company_mission=spec.mission,
        default_agent_quota_mb=spec.default_agent_quota_mb,
        efficiency=efficiency,
        performance=performance,
    )
    handle_ref[0] = handle
    approvals.subscribe(_make_hire_completion_handler(handle_ref, config))
    approvals.subscribe(performance.apply_approval_side_effect)
    _cancel_pending_direct_governance_approvals(handle, by=requested_by)
    _repair_unmanaged_members_to_ceo(handle, by=requested_by)
    return handle


def _bootstrap_core_agents(
    org: Org, requested_by: str,
) -> tuple[IdAgent, IdAgent, IdAgent]:
    ceo = org.add_agent(
        role=Role.CEO, persona_ref="default",
        reports_to=None, requested_by=requested_by,
        via_hr=False, bootstrap=True,
        first_name="Alex", last_name="Morgan",
    )
    hr = org.add_agent(
        role=Role.HR, persona_ref="default",
        reports_to=ceo.id, requested_by=requested_by,
        via_hr=False, bootstrap=True,
        first_name="Jordan", last_name="Lee",
    )
    sec = org.add_agent(
        role=Role.SECURITY, persona_ref="default",
        reports_to=ceo.id, requested_by=requested_by,
        via_hr=False, bootstrap=True,
        first_name="Casey", last_name="Chen",
    )
    return ceo, hr, sec


def _existing_core_ids(org: Org) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in org.all(status=Status.ACTIVE):
        if a.role is Role.CEO and "ceo" not in out:
            out["ceo"] = a.id
        elif a.role is Role.HR and "hr" not in out:
            out["hr"] = a.id
        elif a.role is Role.SECURITY and "security" not in out:
            out["security"] = a.id
    if not {"ceo", "hr", "security"} <= out.keys():
        raise RuntimeError(
            f"company missing bootstrap roles; have {sorted(out)}"
        )
    return out


def rollback_company_dir(root: Path, company_id: str) -> None:
    """Best-effort cleanup of a partially-created company directory."""
    target = root / "companies" / company_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


__all__ = [
    "FactoryConfig",
    "LLMFactory",
    "build_company",
    "rollback_company_dir",
]
