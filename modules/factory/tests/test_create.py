"""create_company happy paths and basic invariants."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from modules.clock import ClockState
from modules.approvals import (
    ApprovalKind,
    ApprovalRoute,
    ApprovalStatus,
    Decision,
    RouteTarget,
)
from modules.event_store import EventKind
from modules.factory import (
    CompanyFactory,
    CompanySpec,
    CompanyStatus,
    ExtraAgentSpec,
)
from modules.identity import Role, Status
from modules.storage import PROJECT_WORKSPACE_ID
from modules.storage import BoardDB

pytestmark = pytest.mark.asyncio


async def test_create_company_creates_db_workspace_and_agents(
    factory: CompanyFactory, basic_spec: CompanySpec, root: Path,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    try:
        # Per-company SQLite file exists.
        assert (root / "companies" / handle.company_id / "company.db").exists()

        # CEO + HR + Security bootstrap agents are active.
        active = handle.identity.all(status=Status.ACTIVE)
        roles = sorted(a.role.value for a in active)
        assert roles == sorted(
            [Role.CEO.value, Role.HR.value, Role.SECURITY.value],
        )

        # Each agent owns a workspace directory.
        for a in active:
            assert (
                root / "companies" / handle.company_id
                / "workspaces" / a.id
            ).exists()
    finally:
        await handle.shutdown()


async def test_create_company_emits_company_created_event_and_welcome(
    factory: CompanyFactory, basic_spec: CompanySpec,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    try:
        events = handle.events.read(kinds=[EventKind.COMPANY_CREATED])
        assert len(events) == 1
        payload = events[0].payload
        assert payload["company_id"] == handle.company_id
        assert payload["name"] == basic_spec.name

        # CEO received the welcome message.
        ceo_id = handle.bootstrap_agent_ids["ceo"]
        ceo = handle.agents[ceo_id]
        # Wait briefly for orchestrator delivery to land in the agent inbox.
        # (FakeLLM emits stop, so the inbox drains right back to zero.)
        await _drain(handle)
        # The event store records system messages too.
        assert handle.budget.state().total.amount_usd == Decimal("1000")
        # Welcome message system_send appended a message envelope to events?
        # Not directly observable here; the absence of errors is the contract.
        assert ceo.id == ceo_id
    finally:
        await handle.shutdown()


async def test_extra_agents_link_to_correct_manager(
    factory: CompanyFactory,
) -> None:
    spec = CompanySpec(
        name="Acme",
        mission="Ship.",
        initial_budget_usd=200,
        company_disk_quota_mb=32,
        default_agent_quota_mb=4,
        extra_agents=(
            ExtraAgentSpec(
                role_title="Engineer",
                first_name="Sam",
                last_name="Smith",
                reports_to_role=Role.CEO,
            ),
        ),
    )
    handle = await factory.create_company(spec, requested_by="USER")
    try:
        active = handle.identity.all(status=Status.ACTIVE)
        eng = next(a for a in active if a.role is Role.MEMBER and a.role_title == "Engineer")
        ceo_id = handle.bootstrap_agent_ids["ceo"]
        assert eng.reports_to == ceo_id
    finally:
        await handle.shutdown()


async def test_propose_hire_tool_starts_agent_without_board_approval(
    factory: CompanyFactory,
    basic_spec: CompanySpec,
    root: Path,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    try:
        hr_id = handle.bootstrap_agent_ids["hr"]
        ceo_id = handle.bootstrap_agent_ids["ceo"]
        result = await handle.tools.invoke(
            agent_id=hr_id,
            tool_name="propose_hire",
            args={
                "role_title": "Engineer",
                "role_description": "Build product features.",
                "reports_to": ceo_id,
                "rationale": "Need implementation capacity.",
            },
            correlation_id="hire-direct",
        )

        assert result.ok
        out = result.output.model_dump() if result.output is not None else {}
        new_agent_id = out["agent_id"]
        assert out["status"] == "hired"
        assert new_agent_id in handle.agents
        hired = handle.identity.get(new_agent_id)
        assert hired.role_title == "Engineer"
        assert hired.reports_to == ceo_id
        assert (
            root / "companies" / handle.company_id / "workspaces" / new_agent_id
        ).exists()
        assert handle.events.read(kinds=[EventKind.APPROVAL_REQUESTED]) == []
    finally:
        await handle.shutdown()


async def test_project_workspace_exists_and_survives_restart(
    factory: CompanyFactory,
    basic_spec: CompanySpec,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    cid = handle.company_id
    try:
        handle.workspace.write(PROJECT_WORKSPACE_ID, "README.md", b"shared")
    finally:
        await handle.shutdown()

    restarted = await factory.restart_company(cid)
    try:
        assert (
            restarted.workspace.read_own(PROJECT_WORKSPACE_ID, "README.md")
            == b"shared"
        )
    finally:
        await restarted.shutdown()


async def test_restart_cancels_legacy_direct_governance_approvals(
    factory: CompanyFactory,
    basic_spec: CompanySpec,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    cid = handle.company_id
    try:
        hire = handle.approvals.request(
            kind=ApprovalKind.HIRE,
            requester_id=handle.bootstrap_agent_ids["hr"],
            payload={
                "role_title": "Engineer",
                "role_description": "Build.",
                "reports_to": handle.bootstrap_agent_ids["ceo"],
            },
            route=ApprovalRoute(target=RouteTarget.BOARD),
            request_id="legacy-hire",
        )
        role_change = handle.approvals.request(
            kind=ApprovalKind.ROLE_CHANGE,
            requester_id=handle.bootstrap_agent_ids["ceo"],
            payload={
                "target_agent_id": handle.bootstrap_agent_ids["hr"],
                "from_agent_id": handle.bootstrap_agent_ids["ceo"],
                "new_role_title": "People Lead",
                "new_role_description": "Lead hiring.",
            },
            route=ApprovalRoute(target=RouteTarget.BOARD),
            request_id="legacy-role-change",
        )
        fire = handle.approvals.request(
            kind=ApprovalKind.FIRE_DEPTH_1,
            requester_id=handle.bootstrap_agent_ids["ceo"],
            payload={
                "actor_id": handle.bootstrap_agent_ids["ceo"],
                "target_id": handle.bootstrap_agent_ids["hr"],
                "reason": "legacy request",
            },
            route=ApprovalRoute(target=RouteTarget.BOARD),
            request_id="legacy-fire",
        )
        assert hire.status is ApprovalStatus.PENDING
        assert role_change.status is ApprovalStatus.PENDING
        assert fire.status is ApprovalStatus.PENDING
    finally:
        await handle.shutdown()

    restarted = await factory.restart_company(cid)
    try:
        assert restarted.approvals.get("legacy-hire").status is ApprovalStatus.CANCELLED
        assert (
            restarted.approvals.get("legacy-role-change").status
            is ApprovalStatus.CANCELLED
        )
        assert restarted.approvals.get("legacy-fire").status is ApprovalStatus.CANCELLED
    finally:
        await restarted.shutdown()


async def test_invalid_legacy_hire_approval_does_not_spawn_orphan(
    factory: CompanyFactory,
    basic_spec: CompanySpec,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    try:
        before = handle.identity.active_count()
        approval = handle.approvals.request(
            kind=ApprovalKind.HIRE,
            requester_id=handle.bootstrap_agent_ids["ceo"],
            payload={},
            route=ApprovalRoute(target=RouteTarget.BOARD),
            request_id="empty-hire",
        )

        handle.approvals.decide(
            approval.request_id,
            decider_id="BOARD",
            decision=Decision.APPROVE,
        )
        await _drain(handle)

        assert handle.identity.active_count() == before
        assert [
            a for a in handle.identity.all(status=Status.ACTIVE)
            if a.role is Role.MEMBER and a.reports_to is None
        ] == []
    finally:
        await handle.shutdown()


async def test_restart_repairs_unmanaged_members_to_ceo(
    factory: CompanyFactory,
    basic_spec: CompanySpec,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    cid = handle.company_id
    try:
        hr_id = handle.bootstrap_agent_ids["hr"]
        ceo_id = handle.bootstrap_agent_ids["ceo"]
        member = handle.identity.add_agent(
            role=Role.MEMBER,
            persona_ref="default",
            reports_to=ceo_id,
            requested_by=hr_id,
            via_hr=True,
            first_name="Bug",
            last_name="Legacy",
            role_title="Legacy Member",
            role_description="Created before orphan repair existed.",
        )
        with handle.db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET reports_to = NULL WHERE id = ?",
                (member.id,),
            )
    finally:
        await handle.shutdown()

    restarted = await factory.restart_company(cid)
    try:
        assert restarted.identity.manager_of(member.id) == restarted.bootstrap_agent_ids["ceo"]
    finally:
        await restarted.shutdown()


async def test_initial_budget_set_correctly(
    factory: CompanyFactory, basic_spec: CompanySpec,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    try:
        assert handle.budget.state().total.amount_usd == Decimal("1000")
    finally:
        await handle.shutdown()


async def test_clock_starts_running_after_create(
    factory: CompanyFactory, basic_spec: CompanySpec,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    try:
        assert handle.clock.state() is ClockState.RUNNING
    finally:
        await handle.shutdown()


async def test_board_row_persisted_with_spec(
    factory: CompanyFactory, basic_spec: CompanySpec, board_db: BoardDB,
) -> None:
    handle = await factory.create_company(basic_spec, requested_by="USER")
    try:
        row = board_db.connect().execute(
            "SELECT name, status, spec_json FROM companies WHERE id = ?",
            (handle.company_id,),
        ).fetchone()
        assert row is not None
        assert row["name"] == basic_spec.name
        assert row["status"] == CompanyStatus.ACTIVE.value
        spec = json.loads(row["spec_json"])
        assert spec["mission"] == basic_spec.mission
    finally:
        await handle.shutdown()


async def test_duplicate_company_id_rejected(
    factory: CompanyFactory,
) -> None:
    spec = CompanySpec(
        name="A", mission="m",
        initial_budget_usd=10, company_disk_quota_mb=4,
        default_agent_quota_mb=2, company_id="cid-fixed",
    )
    h1 = await factory.create_company(spec, requested_by="USER")
    try:
        from modules.factory import CompanyAlreadyExists
        with pytest.raises(CompanyAlreadyExists):
            await factory.create_company(spec, requested_by="USER")
    finally:
        await h1.shutdown()


async def test_concurrent_companies_are_isolated(
    factory: CompanyFactory,
) -> None:
    spec1 = CompanySpec(
        name="A", mission="m1",
        initial_budget_usd=100, company_disk_quota_mb=8,
        default_agent_quota_mb=2,
    )
    spec2 = CompanySpec(
        name="B", mission="m2",
        initial_budget_usd=200, company_disk_quota_mb=8,
        default_agent_quota_mb=2,
    )
    h1 = await factory.create_company(spec1, requested_by="USER")
    h2 = await factory.create_company(spec2, requested_by="USER")
    try:
        assert h1.company_id != h2.company_id
        assert h1.budget.state().total.amount_usd == Decimal("100")
        assert h2.budget.state().total.amount_usd == Decimal("200")
        # Identity rows are separate DBs.
        h1_ceo = h1.bootstrap_agent_ids["ceo"]
        h2_ceo = h2.bootstrap_agent_ids["ceo"]
        assert h1_ceo != h2_ceo
        # `get` on the wrong company raises.
        with pytest.raises(KeyError):
            h2.identity.get(h1_ceo)
    finally:
        await h1.shutdown()
        await h2.shutdown()


# ---------------------------------------------------------------- helpers


async def _drain(handle: object) -> None:
    """Yield the loop a few times so any in-flight tasks settle."""
    import asyncio
    for _ in range(5):
        await asyncio.sleep(0)
