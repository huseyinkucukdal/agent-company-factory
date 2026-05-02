"""E2E: link request \u2192 approve \u2192 cross-company send via connector.

Covers the inter-company plan scenario: two real companies created
through the integrated runtime, board approves a peer link, an agent in
company A invokes the auto-registered ``inter_company.send`` connector
service, and the message lands in company B's HR inbox.
"""
from __future__ import annotations

import pytest

from modules.board_api.links import LinkRelationship, LinkScope
from modules.identity import Role
from modules.inter_company import CrossMessageKind, CrossStatus
from modules.integration import IntegratedRuntime

from .conftest import make_spec

pytestmark = pytest.mark.asyncio


async def test_inter_company_link_and_send(
    runtime: IntegratedRuntime,
) -> None:
    handle_a = await runtime.factory.create_company(
        make_spec(name="Alpha"), requested_by="USER",
    )
    handle_b = await runtime.factory.create_company(
        make_spec(name="Beta"), requested_by="USER",
    )

    try:
        # Both companies have the inter_company connector service wired
        # up automatically by the integration runtime.
        assert handle_a.connector.get_service("inter_company") is not None
        assert handle_b.connector.get_service("inter_company") is not None

        # Board user requests + approves the link.
        link = runtime.inter_company._links.request(  # noqa: SLF001
            from_company=handle_a.company_id,
            to_company=handle_b.company_id,
            relationship=LinkRelationship.PEER,
            scope=LinkScope(
                allowed_messages=("inquiry", "quote", "generic"),
                rate_limit_per_hour=100,
                max_payload_bytes=8192,
            ),
            requested_by="USER",
        )
        approved = runtime.inter_company._links.decide(  # noqa: SLF001
            link.id, decision="approve", decided_by="USER", note=None,
        )
        assert approved.status.value == "approved"

        # Send a cross-company message through the runtime service.
        from modules.inter_company import CrossMessage
        ceo_a = handle_a.bootstrap_agent_ids["ceo"]
        result = await runtime.inter_company.send_cross(
            link_id=link.id,
            from_agent=ceo_a,
            from_company=handle_a.company_id,
            message=CrossMessage(
                kind=CrossMessageKind.INQUIRY,
                subject="quote please",
                body="What is your hourly rate?",
            ),
        )
        assert result.status is CrossStatus.DELIVERED

        # The HR agent in company B is the default inbox; confirm an
        # outbox row exists with the correct target.
        b_hr = next(
            a.id for a in handle_b.identity.all() if a.role is Role.HR
        )
        assert result.target_agent == b_hr

        rows = runtime.inter_company.list_outbox(link_id=link.id)
        assert len(rows) == 1
        assert rows[0].status is CrossStatus.DELIVERED
        assert rows[0].target_agent == b_hr
    finally:
        await handle_a.shutdown()
        await handle_b.shutdown()
