"""Registry, role gating, and Claude-SDK schema rendering."""
from __future__ import annotations

import pytest

from modules.identity import Role
from modules.tools import (
    BUILTIN_TOOLS,
    ApprovalRequirement,
    ToolCategory,
    ToolDef,
    Tools,
)
from modules.tools.builtin import (
    SendMessageArgs,
    SendMessageOut,
    _exec_send_message,
)


def test_register_and_for_role(tools: Tools) -> None:
    # All built-ins registered.
    assert {t.name for t in tools.all_tools()} == {b.name for b in BUILTIN_TOOLS}

    # CEO can see role-restricted governance tools.
    ceo_names = {t.name for t in tools.for_role(Role.CEO)}
    assert "report_to_board" in ceo_names
    assert "propose_hire" not in ceo_names  # only HR can propose_hire

    # HR can propose_hire.
    hr_names = {t.name for t in tools.for_role(Role.HR)}
    assert "propose_hire" in hr_names

    # MEMBER cannot propose_hire or report_to_board.
    member_names = {t.name for t in tools.for_role(Role.MEMBER)}
    assert "report_to_board" not in member_names
    assert "propose_hire" not in member_names
    assert "send_message" in member_names  # any-role tool still visible


def test_register_duplicate_rejected(tools: Tools) -> None:
    dup = ToolDef(
        name="send_message",
        description="dup",
        schema=SendMessageArgs,
        output_schema=SendMessageOut,
        category=ToolCategory.COMMUNICATION,
        roles_allowed="any",
        executor=_exec_send_message,
    )
    with pytest.raises(ValueError, match="already registered"):
        tools.register(dup)


def test_executorless_tool_rejected(tools: Tools) -> None:
    bad = ToolDef(
        name="bogus",
        description="x",
        schema=SendMessageArgs,
        output_schema=SendMessageOut,
        category=ToolCategory.COMMUNICATION,
        roles_allowed="any",
        executor=None,
    )
    with pytest.raises(ValueError, match="no executor"):
        tools.register(bad)


def test_claude_sdk_schema_shape(tools: Tools) -> None:
    schema = tools.claude_sdk_schema(Role.CEO)
    assert isinstance(schema, list) and schema
    sample = schema[0]
    assert set(sample) == {"name", "description", "input_schema"}
    # input_schema is a JSON-schema-compatible dict.
    assert sample["input_schema"]["type"] == "object"


def test_approval_requirement_table() -> None:
    req = {t.name: t.requires_approval for t in BUILTIN_TOOLS}
    assert req["external_call"] is ApprovalRequirement.EXTERNAL
    assert req["pay_invoice"] is ApprovalRequirement.EXPENSE
    assert req["register_subscription"] is ApprovalRequirement.EXPENSE
    assert req["send_message"] is ApprovalRequirement.NONE
    assert req["propose_hire"] is ApprovalRequirement.NONE
