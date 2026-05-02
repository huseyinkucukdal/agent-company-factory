"""Role guards for Security Agent behaviour."""
from __future__ import annotations

from modules.identity import Org, Role
from modules.identity.models import FireResult


def test_security_agent_cannot_fire_anyone(
    org: Org, bootstrap: dict[str, str],
) -> None:
    # Add a junior engineer that reports to ENG to give SECURITY a path.
    junior = org.add_agent(
        role=Role.MEMBER, persona_ref="eng.v1",
        reports_to=bootstrap["eng"],
        requested_by=bootstrap["hr"], via_hr=True,
    )
    outcome = org.fire(
        actor_id=bootstrap["sec"],
        target_id=junior.id,
        reason="exfiltration_attempt",
        via_hr=True,
    )
    # Even with via_hr, SECURITY isn't in the chain → not_subordinate.
    assert outcome.result is FireResult.DENIED


def test_security_agent_can_be_fired_by_manager_without_board_approval(
    org: Org, bootstrap: dict[str, str],
) -> None:
    outcome = org.fire(
        actor_id=bootstrap["ceo"],
        target_id=bootstrap["sec"],
        reason="redundant",
        via_hr=True,
    )
    assert outcome.result is FireResult.DIRECT_FIRED
