"""Smoke tests for built-in tool wiring."""
from __future__ import annotations

import pytest

from modules.approvals import Approvals, ApprovalRoute, RouteTarget
from modules.identity import Agent, Org, Status
from modules.tools import Tools
from modules.tools.tests.conftest import FakeConnector, FakeHireService


@pytest.mark.asyncio
async def test_query_org_chart(tools: Tools, agents: dict[str, Agent]) -> None:
    res = await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="query_org_chart",
        args={},
        correlation_id="cid-org",
    )
    assert res.ok
    assert res.output is not None
    out = res.output.model_dump()
    assert out["self_id"] == agents["eng"].id
    assert out["manager_id"] == agents["ceo"].id


@pytest.mark.asyncio
async def test_escalate_routes_to_manager(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    res = await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="escalate_to_manager",
        args={"body": "blocked"},
        correlation_id="cid-esc",
    )
    assert res.ok
    assert res.output is not None
    assert res.output.model_dump()["delivered_to"] == agents["ceo"].id


@pytest.mark.asyncio
async def test_summarize_and_remember(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    res = await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="summarize_and_remember",
        args={"summary": "Built a thing successfully."},
        correlation_id="cid-sum",
    )
    assert res.ok
    assert res.output is not None
    assert res.output.model_dump()["memory_id"]


@pytest.mark.asyncio
async def test_propose_hire_hires_directly(
    tools: Tools, agents: dict[str, Agent], approvals: Approvals
) -> None:
    res = await tools.invoke(
        agent_id=agents["hr"].id,
        tool_name="propose_hire",
        args={
            "first_name": "Sam",
            "last_name": "Smith",
            "role_title": "Engineer",
            "role_description": "Write backend code",
            "reports_to": agents["ceo"].id,
            "rationale": "need more help",
        },
        correlation_id="cid-hire",
    )
    assert res.ok
    out = res.output.model_dump() if res.output is not None else {}
    assert out["status"] == "hired"
    assert out["agent_id"]
    assert approvals.pending_for(ApprovalRoute(target=RouteTarget.BOARD)) == []


@pytest.mark.asyncio
async def test_propose_hire_sanitizes_placeholder_names(
    tools: Tools,
    agents: dict[str, Agent],
    hire_service: FakeHireService,
) -> None:
    args = {
        "first_name": "TBD",
        "last_name": "New Product Designer",
        "role_title": "Product Designer",
        "role_description": "Design the product.\nReports To: HR Manager",
        "reports_to": agents["ceo"].id,
        "rationale": "need product design help",
    }
    result = await tools.invoke(
        agent_id=agents["hr"].id,
        tool_name="propose_hire",
        args=args,
        correlation_id="cid-hire-placeholder-1",
    )

    assert result.ok
    payload = hire_service.requests[-1]["payload"]
    assert "first_name" not in payload
    assert "last_name" not in payload
    assert "Reports To:" not in payload["role_description"]


@pytest.mark.asyncio
async def test_propose_fire_fires_directly_without_approval(
    tools: Tools,
    agents: dict[str, Agent],
    approvals: Approvals,
    org: Org,
) -> None:
    result = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="propose_fire",
        args={
            "target_agent_id": agents["eng"].id,
            "reason": "role eliminated",
        },
        correlation_id="cid-fire-direct",
    )

    assert result.ok
    out = result.output.model_dump() if result.output is not None else {}
    assert out["status"] == "direct_fired"
    assert out["request_id"] == ""
    assert org.get(agents["eng"].id).status is Status.FIRED
    assert approvals.pending_for(ApprovalRoute(target=RouteTarget.BOARD)) == []


@pytest.mark.asyncio
async def test_project_workspace_tools_share_files(
    tools: Tools,
    agents: dict[str, Agent],
) -> None:
    write = await tools.invoke(
        agent_id=agents["eng"].id,
        tool_name="write_project_workspace",
        args={"path": "src/app.js", "content": "export const ok = true;"},
        correlation_id="cid-project-write",
    )
    assert write.ok

    read = await tools.invoke(
        agent_id=agents["cfo"].id,
        tool_name="read_project_workspace",
        args={"path": "src/app.js"},
        correlation_id="cid-project-read",
    )
    assert read.ok
    assert read.output is not None
    assert read.output.model_dump()["content"] == "export const ok = true;"

    listing = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="list_project_workspace",
        args={"path": "src"},
        correlation_id="cid-project-list",
    )
    assert listing.ok
    assert listing.output is not None
    assert listing.output.model_dump()["entries"][0]["relative_path"] == "src/app.js"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["hire", "role_change", "fire_depth_1"],
)
async def test_request_approval_rejects_direct_governance_kind(
    kind: str,
    tools: Tools,
    agents: dict[str, Agent],
    approvals: Approvals,
) -> None:
    result = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="request_approval",
        args={"kind": kind},
        correlation_id=f"cid-request-{kind}-approval",
    )

    assert not result.ok
    assert result.error_code == "permission_denied"
    assert f"approval_not_required:{kind}" in (result.error_message or "")
    assert approvals.pending_for(ApprovalRoute(target=RouteTarget.BOARD)) == []


@pytest.mark.asyncio
async def test_external_call_executes_via_connector(
    tools: Tools,
    agents: dict[str, Agent],
    approvals: Approvals,
    connector: FakeConnector,
) -> None:
    from modules.approvals import Decision

    cid = "cid-conn"
    await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="external_call",
        args={
            "service": "openai",
            "endpoint": "/v1/chat",
            "payload": {"prompt": "x"},
            "estimated_cost_usd": "0.10",
        },
        correlation_id=cid,
    )
    approvals.decide(cid, "BOARD", Decision.APPROVE)
    res = await tools.invoke(
        agent_id=agents["ceo"].id,
        tool_name="external_call",
        args={
            "service": "openai",
            "endpoint": "/v1/chat",
            "payload": {"prompt": "x"},
            "estimated_cost_usd": "0.10",
        },
        correlation_id=cid,
    )
    assert res.ok
    assert connector.calls
    assert connector.calls[0][0] == "openai"


@pytest.mark.asyncio
async def test_list_my_tasks_and_approvals(
    tools: Tools, agents: dict[str, Agent]
) -> None:
    for name in ("list_my_tasks", "list_my_approvals"):
        res = await tools.invoke(
            agent_id=agents["eng"].id,
            tool_name=name,
            args={},
            correlation_id=f"cid-{name}",
        )
        assert res.ok
        assert res.output is not None
        assert res.output.model_dump() == {"items": []}
