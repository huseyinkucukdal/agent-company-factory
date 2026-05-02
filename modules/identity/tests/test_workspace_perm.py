"""Workspace permission delegation."""
from __future__ import annotations

from modules.identity import Org, Role

from .conftest import bootstrap_ceo, bootstrap_hr, hire


def test_can_read_workspace_self_true(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    assert org.can_read_workspace(ceo, ceo) is True


def test_can_read_workspace_manager_true(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    eng = hire(org, Role.MEMBER, cto)
    assert org.can_read_workspace(cto, eng) is True


def test_can_read_workspace_grandmanager_false(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    eng = hire(org, Role.MEMBER, cto)
    # CEO is grand-manager but only direct manager has read access.
    assert org.can_read_workspace(ceo, eng) is False


def test_can_read_workspace_unrelated_false(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    cfo = hire(org, Role.MEMBER, ceo)
    a = hire(org, Role.MEMBER, cto)
    b = hire(org, Role.MEMBER, cfo)
    assert org.can_read_workspace(a, b) is False


def test_can_read_workspace_unknown_owner_false(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    assert org.can_read_workspace(ceo, "ghost") is False
