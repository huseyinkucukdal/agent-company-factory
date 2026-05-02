"""CEO / HR / Security guards."""
from __future__ import annotations

from modules.identity import FireResult, Org, Role, Status

from .conftest import bootstrap_ceo, bootstrap_hr, hire


def test_fire_ceo_denied(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    hr = bootstrap_hr(org, ceo)
    out = org.fire(actor_id=hr, target_id=ceo, reason="performance below bar", via_hr=True)
    assert out.result is FireResult.DENIED
    assert out.reason == "ceo_cannot_be_fired_directly"


def test_security_can_be_fired_by_manager_without_board_approval(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    bootstrap_hr(org, ceo)
    sec = hire(org, Role.SECURITY, ceo)
    out = org.fire(actor_id=ceo, target_id=sec, reason="performance below bar", via_hr=True)
    assert out.result is FireResult.DIRECT_FIRED
    assert org.get(sec).status is Status.FIRED


def test_hr_fire_blocked_until_replacement(org: Org) -> None:
    """Firing the only HR is denied; once a replacement HR is active it works."""
    ceo = bootstrap_ceo(org)
    hr1 = bootstrap_hr(org, ceo)

    out = org.fire(actor_id=ceo, target_id=hr1, reason="performance below bar", via_hr=True)
    assert out.result is FireResult.DENIED
    assert out.reason == "no_hr_replacement"

    # After CEO hires another HR the original can be fired.
    hire(org, Role.HR, ceo)
    out2 = org.fire(actor_id=ceo, target_id=hr1, reason="performance below bar", via_hr=True)
    assert out2.result is FireResult.DIRECT_FIRED
    assert org.get(hr1).status is Status.FIRED


def test_is_special_and_has_role(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    hr = bootstrap_hr(org, ceo)
    eng = hire(org, Role.MEMBER, ceo)

    assert org.is_special(ceo) is True
    assert org.is_special(hr) is True
    assert org.is_special(eng) is False
    assert org.has_role(Role.CEO) is True
    assert org.has_role(Role.SECURITY) is False


def test_last_remaining_hr_stays_protected_after_direct_fire(org: Org) -> None:
    ceo = bootstrap_ceo(org)
    hr1 = bootstrap_hr(org, ceo)
    hr2 = hire(org, Role.HR, ceo)

    out = org.fire(actor_id=ceo, target_id=hr1, reason="performance below bar", via_hr=True)
    assert out.result is FireResult.DIRECT_FIRED
    assert org.get(hr1).status is Status.FIRED

    out2 = org.fire(actor_id=ceo, target_id=hr2, reason="leaving", via_hr=True)
    assert out2.result is FireResult.DENIED
    assert out2.reason == "no_hr_replacement"
    assert org.get(hr2).status is Status.ACTIVE
