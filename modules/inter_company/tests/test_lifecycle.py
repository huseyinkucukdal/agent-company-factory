"""Pause / queue / replay + revoke / suspend tests."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from modules.board_api.links import LinkRelationship, LinkScope, LinkService
from modules.inter_company import (
    CrossMessage,
    CrossMessageKind,
    CrossStatus,
    InterCompanyService,
    LinkNotApproved,
)
from modules.orchestrator import SendResult


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _msg() -> CrossMessage:
    return CrossMessage(
        kind=CrossMessageKind.INQUIRY, subject="hi", body="hello",
    )


def test_target_paused_message_queued(
    service: InterCompanyService, approved_link, factory: Any,
) -> None:
    factory.handles["co_b"].orchestrator.paused = True
    result = _run(
        service.send_cross(
            link_id=approved_link.id, from_agent="x",
            from_company="co_a", message=_msg(),
        ),
    )
    assert result.status is CrossStatus.QUEUED
    assert factory.handles["co_b"].orchestrator.delivered == []
    rows = service.list_outbox(link_id=approved_link.id)
    assert len(rows) == 1
    assert rows[0].status is CrossStatus.QUEUED


def test_resume_flushes_queued_messages(
    service: InterCompanyService, approved_link, factory: Any,
) -> None:
    target_orch = factory.handles["co_b"].orchestrator
    target_orch.paused = True
    for i in range(3):
        _run(
            service.send_cross(
                link_id=approved_link.id, from_agent="x",
                from_company="co_a",
                message=CrossMessage(
                    kind=CrossMessageKind.INQUIRY,
                    subject=f"#{i}", body=f"body {i}",
                ),
            ),
        )
    target_orch.paused = False
    flushed = _run(service.deliver_pending("co_b"))
    assert flushed == 3
    assert len(target_orch.delivered) == 3
    rows = service.list_outbox(company="co_b")
    assert all(r.status is CrossStatus.DELIVERED for r in rows)


def test_replay_skips_revoked_links(
    service: InterCompanyService,
    approved_link,
    links: LinkService,
    factory: Any,
) -> None:
    factory.handles["co_b"].orchestrator.paused = True
    result = _run(
        service.send_cross(
            link_id=approved_link.id, from_agent="x",
            from_company="co_a", message=_msg(),
        ),
    )
    assert result.status is CrossStatus.QUEUED
    links.revoke(approved_link.id, by="u_admin", reason="cleanup")
    factory.handles["co_b"].orchestrator.paused = False
    flushed = _run(service.deliver_pending("co_b"))
    assert flushed == 1
    rows = service.list_outbox(link_id=approved_link.id)
    assert rows[0].status is CrossStatus.REJECTED
    assert rows[0].reason == "link_not_approved"


def test_revoked_link_blocks_future_sends(
    service: InterCompanyService, approved_link, links: LinkService,
) -> None:
    links.revoke(approved_link.id, by="u", reason="x")
    with pytest.raises(LinkNotApproved):
        _run(
            service.send_cross(
                link_id=approved_link.id, from_agent="x",
                from_company="co_a", message=_msg(),
            ),
        )


def test_suspended_link_blocks_then_resume_unblocks(
    service: InterCompanyService, approved_link, links: LinkService,
) -> None:
    links.suspend(approved_link.id, by="u", reason="cooling")
    with pytest.raises(LinkNotApproved):
        _run(
            service.send_cross(
                link_id=approved_link.id, from_agent="x",
                from_company="co_a", message=_msg(),
            ),
        )
    links.resume(approved_link.id, by="u")
    result = _run(
        service.send_cross(
            link_id=approved_link.id, from_agent="x",
            from_company="co_a", message=_msg(),
        ),
    )
    assert result.status is CrossStatus.DELIVERED


def test_company_close_auto_revokes_links(
    service: InterCompanyService, approved_link, links: LinkService,
) -> None:
    n = service.on_company_closed("co_a", by="system")
    assert n == 1
    refreshed = links.get(approved_link.id)
    assert refreshed.status.value == "revoked"


def test_outbox_records_event_mirror(
    service: InterCompanyService, approved_link, factory: Any,
) -> None:
    _run(
        service.send_cross(
            link_id=approved_link.id, from_agent="x",
            from_company="co_a", message=_msg(),
        ),
    )
    src_log = factory.handles["co_a"].events.log
    tgt_log = factory.handles["co_b"].events.log
    # Each side gets at least one mirrored EXTERNAL_CALL event.
    src_kinds = [k for k, _, _ in src_log]
    tgt_kinds = [k for k, _, _ in tgt_log]
    assert any("external_call" in str(k) for k in src_kinds)
    assert any("external_call" in str(k) for k in tgt_kinds)


def test_idempotent_outbox_id(
    service: InterCompanyService, approved_link,
) -> None:
    rid = "fixed-rid-1"
    r1 = _run(
        service.send_cross(
            link_id=approved_link.id, from_agent="x",
            from_company="co_a", message=_msg(), request_id=rid,
        ),
    )
    assert r1.id == rid
    # Re-using the same rid should fail at the unique PK level.
    import sqlite3 as _sqlite

    with pytest.raises(_sqlite.IntegrityError):
        _run(
            service.send_cross(
                link_id=approved_link.id, from_agent="x",
                from_company="co_a", message=_msg(), request_id=rid,
            ),
        )
