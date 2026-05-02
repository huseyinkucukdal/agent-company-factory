"""Cross-message lifecycle: happy path + scope/state checks."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from modules.board_api.links import (
    LinkRelationship,
    LinkScope,
    LinkService,
    LinkStatus,
)
from modules.factory import CompanyStatus
from modules.inter_company import (
    CrossMessage,
    CrossMessageKind,
    CrossRateLimited,
    CrossStatus,
    DirectionMismatch,
    InterCompanyService,
    LinkNotApproved,
    LinkNotFound,
    PayloadTooLarge,
    ScopeViolation,
    TargetUnavailable,
)
from modules.orchestrator import SendResult


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _msg(
    kind: CrossMessageKind = CrossMessageKind.INQUIRY,
    body: str = "Quote please.",
    subject: str = "RFQ #42",
) -> CrossMessage:
    return CrossMessage(kind=kind, subject=subject, body=body)


# ----------------------------------------------------- 1. Lifecycle

def test_send_after_approval_routes_to_target_inbox(
    service: InterCompanyService, approved_link, factory: Any,
) -> None:
    result = _run(
        service.send_cross(
            link_id=approved_link.id,
            from_agent="a_eng_1",
            from_company="co_a",
            message=_msg(),
        ),
    )
    assert result.status is CrossStatus.DELIVERED
    assert result.reason is None
    assert result.target_agent == "b_hr"
    inbox = factory.handles["co_b"].orchestrator.delivered
    assert len(inbox) == 1
    to_agent, content, _kind, corr = inbox[0]
    assert to_agent == "b_hr"
    assert "INTER-COMPANY inquiry" in content
    assert "from=co_a" in content
    assert "Subject: RFQ #42" in content
    assert "Quote please." in content
    assert corr == result.id


def test_inbox_falls_back_to_ceo_when_no_hr(
    service: InterCompanyService, approved_link, factory: Any,
) -> None:
    # Drop HR in target.
    factory.handles["co_b"].identity.agents = [
        a for a in factory.handles["co_b"].identity.agents
        if a.role.value != "hr"
    ]
    result = _run(
        service.send_cross(
            link_id=approved_link.id, from_agent="x", from_company="co_a",
            message=_msg(),
        ),
    )
    assert result.status is CrossStatus.DELIVERED
    assert result.target_agent == "b_ceo"


def test_no_inbox_agent_marks_rejected(
    service: InterCompanyService, approved_link, factory: Any,
) -> None:
    factory.handles["co_b"].identity.agents = []
    result = _run(
        service.send_cross(
            link_id=approved_link.id, from_agent="x", from_company="co_a",
            message=_msg(),
        ),
    )
    assert result.status is CrossStatus.REJECTED
    assert result.reason == "no_inbox_agent"


def test_send_unknown_link_raises(service: InterCompanyService) -> None:
    with pytest.raises(LinkNotFound):
        _run(
            service.send_cross(
                link_id="does-not-exist", from_agent="x",
                from_company="co_a", message=_msg(),
            ),
        )


def test_send_before_approval_raises(
    service: InterCompanyService, links: LinkService,
) -> None:
    pending = links.request(
        from_company="co_a", to_company="co_b",
        relationship=LinkRelationship.PEER,
        scope=LinkScope(allowed_messages=("inquiry",)),
        requested_by="u",
    )
    with pytest.raises(LinkNotApproved):
        _run(
            service.send_cross(
                link_id=pending.id, from_agent="x", from_company="co_a",
                message=_msg(),
            ),
        )


def test_send_with_wrong_direction_raises(
    service: InterCompanyService, approved_link,
) -> None:
    # Link was created A->B; sending as B is rejected (separate link
    # required for reverse).
    with pytest.raises(DirectionMismatch):
        _run(
            service.send_cross(
                link_id=approved_link.id, from_agent="x",
                from_company="co_b", message=_msg(),
            ),
        )


def test_send_target_closed_raises(
    service: InterCompanyService, approved_link, factory: Any,
) -> None:
    factory.close_company("co_b")
    with pytest.raises(TargetUnavailable):
        _run(
            service.send_cross(
                link_id=approved_link.id, from_agent="x",
                from_company="co_a", message=_msg(),
            ),
        )
