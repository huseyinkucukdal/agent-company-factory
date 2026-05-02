"""Persona template tests."""
from __future__ import annotations

import pytest

from modules.agent_runtime import (
    InMemoryPersonaLoader,
    PersonaLoadError,
    default_persona_loader,
    render,
)
from modules.identity import Role


def test_render_substitutes_variables() -> None:
    out = render("hello {{name}}", {"name": "world"})
    assert out == "hello world"


def test_render_missing_variable_raises() -> None:
    with pytest.raises(PersonaLoadError):
        render("hi {{who}}", {})


def test_loader_falls_back_to_default() -> None:
    loader = default_persona_loader()
    out = loader.load(
        Role.CEO, "any-ref",
        {
            "agent_id": "a1", "role": "ceo", "company_id": "c1",
            "manager_name": "(none)", "direct_reports": "(none)",
            "company_mission": "win", "available_tools": "send_message",
            "agent_name": "Alex Morgan", "first_name": "Alex", "last_name": "Morgan",
            "role_title": "ceo", "role_description": "",
        },
    )
    assert "ceo" in out


def test_loader_missing_role_raises() -> None:
    loader = InMemoryPersonaLoader()
    with pytest.raises(PersonaLoadError):
        loader.load(Role.CEO, "x", {})
