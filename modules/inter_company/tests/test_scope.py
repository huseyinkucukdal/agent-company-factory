"""Scope-enforcement + rate-limit tests."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from modules.board_api.links import LinkRelationship, LinkScope, LinkService
from modules.inter_company import (
    CrossMessage,
    CrossMessageKind,
    CrossRateLimited,
    CrossStatus,
    InterCompanyService,
    PayloadTooLarge,
    ScopeViolation,
)


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_link(
    links: LinkService, *, allowed: tuple[str, ...], cap: int = 100,
    max_bytes: int = 8192,
) -> Any:
    link = links.request(
        from_company="co_a", to_company="co_b",
        relationship=LinkRelationship.PEER,
        scope=LinkScope(
            allowed_messages=allowed,
            rate_limit_per_hour=cap,
            max_payload_bytes=max_bytes,
        ),
        requested_by="u",
    )
    return links.decide(link.id, decision="approve", decided_by="u", note=None)


def test_disallowed_kind_rejected(
    service: InterCompanyService, links: LinkService,
) -> None:
    link = _make_link(links, allowed=("inquiry", "quote"))
    with pytest.raises(ScopeViolation):
        _run(
            service.send_cross(
                link_id=link.id, from_agent="x", from_company="co_a",
                message=CrossMessage(
                    kind=CrossMessageKind.ORDER,
                    subject="X", body="Y",
                ),
            ),
        )


def test_payload_size_rejected(
    service: InterCompanyService, links: LinkService,
) -> None:
    link = _make_link(links, allowed=("generic",), max_bytes=64)
    with pytest.raises(PayloadTooLarge):
        _run(
            service.send_cross(
                link_id=link.id, from_agent="x", from_company="co_a",
                message=CrossMessage(
                    kind=CrossMessageKind.GENERIC,
                    subject="X", body="A" * 200,
                ),
            ),
        )


def test_rate_limit_per_hour(
    service: InterCompanyService, links: LinkService,
) -> None:
    link = _make_link(links, allowed=("generic",), cap=3)
    msg = CrossMessage(
        kind=CrossMessageKind.GENERIC, subject="hi", body="ok",
    )
    for _ in range(3):
        result = _run(
            service.send_cross(
                link_id=link.id, from_agent="x",
                from_company="co_a", message=msg,
            ),
        )
        assert result.status is CrossStatus.DELIVERED
    with pytest.raises(CrossRateLimited):
        _run(
            service.send_cross(
                link_id=link.id, from_agent="x",
                from_company="co_a", message=msg,
            ),
        )


def test_empty_allowed_means_all_kinds_pass(
    service: InterCompanyService, links: LinkService,
) -> None:
    link = _make_link(links, allowed=())  # empty == all
    for kind in CrossMessageKind:
        result = _run(
            service.send_cross(
                link_id=link.id, from_agent="x", from_company="co_a",
                message=CrossMessage(kind=kind, subject="s", body="b"),
            ),
        )
        assert result.status is CrossStatus.DELIVERED


def test_body_sanitization_strips_control_chars(
    service: InterCompanyService, links: LinkService, factory: Any,
) -> None:
    link = _make_link(links, allowed=("generic",))
    _run(
        service.send_cross(
            link_id=link.id, from_agent="x", from_company="co_a",
            message=CrossMessage(
                kind=CrossMessageKind.GENERIC,
                subject="ok\x01here",
                body="hello\x00\x07world\nline2",
            ),
        ),
    )
    inbox = factory.handles["co_b"].orchestrator.delivered
    _, content, _, _ = inbox[0]
    assert "\x00" not in content
    assert "\x01" not in content
    assert "\x07" not in content
    assert "helloworld" in content  # control bytes removed in-place
    assert "line2" in content        # \n preserved
