"""CRUD + cycle prevention tests."""
from __future__ import annotations

import pytest

from modules.identity import (
    MAX_CEO_DIRECT_REPORTS,
    MAX_MANAGER_DIRECT_REPORTS,
    HireDenied,
    Org,
    Role,
    Status,
)

from .conftest import bootstrap_ceo, bootstrap_hr, hire


def test_bootstrap_can_skip_via_hr(org: Org) -> None:
    ceo_id = bootstrap_ceo(org)
    a = org.get(ceo_id)
    assert a.role is Role.CEO
    assert a.status is Status.ACTIVE
    assert a.reports_to is None


def test_add_agent_requires_via_hr(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    with pytest.raises(HireDenied):
        org.add_agent(
            role=Role.MEMBER,
            persona_ref="eng.v1",
            reports_to=ceo,
            requested_by=ceo,
            via_hr=False,
        )


def test_cycle_insert_rejected(org: Org) -> None:
    """``reports_to`` must reference an existing active agent."""
    with pytest.raises(HireDenied):
        org.add_agent(
            role=Role.MEMBER,
            persona_ref="eng.v1",
            reports_to="does-not-exist",
            requested_by="hr",
            via_hr=True,
        )


def test_non_bootstrap_hire_requires_manager(org: Org) -> None:
    bootstrap_ceo(org)
    with pytest.raises(HireDenied, match="manager_required"):
        org.add_agent(
            role=Role.MEMBER,
            persona_ref="eng.v1",
            reports_to=None,
            requested_by="hr",
            via_hr=True,
        )


def test_org_graph_basic(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    hr = bootstrap_hr(org, ceo)
    cto = hire(org, Role.MEMBER, ceo)
    eng = hire(org, Role.MEMBER, cto)

    assert org.manager_of(eng) == cto
    assert org.manager_of(ceo) is None
    assert set(org.direct_reports(ceo)) == {hr, cto}
    assert set(org.all_descendants(ceo)) == {hr, cto, eng}
    assert org.chain_up(eng) == [cto, ceo]
    assert org.depth(ceo, eng) == 2
    assert org.depth(cto, eng) == 1
    assert org.depth(eng, ceo) is None
    assert org.depth(ceo, ceo) == 0


def test_direct_report_limits(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)

    # HR already consumes one CEO direct-report slot.
    for _ in range(MAX_CEO_DIRECT_REPORTS - 1):
        hire(org, Role.MEMBER, ceo)
    assert org.direct_report_count(ceo) == MAX_CEO_DIRECT_REPORTS
    with pytest.raises(HireDenied, match=f"direct_report_limit_exceeded:{ceo}:5"):
        hire(org, Role.MEMBER, ceo)

    manager = org.direct_reports(ceo)[-1]
    for _ in range(MAX_MANAGER_DIRECT_REPORTS):
        hire(org, Role.MEMBER, manager)
    assert org.direct_report_count(manager) == MAX_MANAGER_DIRECT_REPORTS
    with pytest.raises(HireDenied, match=f"direct_report_limit_exceeded:{manager}:4"):
        hire(org, Role.MEMBER, manager)


def test_all_filters_by_status(org: Org) -> None:
    bootstrap_ceo(org)
    assert len(org.all()) == 1
    assert len(org.all(status=Status.FIRED)) == 0


def test_company_headcount_limit_is_100_active_agents(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)

    # 2 active bootstrap agents + 98 hires = 100. Fill breadth-first so
    # per-manager direct-report caps are respected.
    managers = [ceo]
    while org.active_count() < 100:
        manager = next(
            mid for mid in managers
            if org.direct_report_count(mid) < (
                MAX_CEO_DIRECT_REPORTS
                if org.get(mid).role is Role.CEO
                else MAX_MANAGER_DIRECT_REPORTS
            )
        )
        hired = hire(org, Role.MEMBER, manager)
        managers.append(hired)

    assert org.active_count() == 100
    with pytest.raises(HireDenied, match="company_headcount_limit_exceeded:100"):
        hire(org, Role.MEMBER, managers[-1])
