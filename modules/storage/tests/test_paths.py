"""Path traversal & resolution tests.

These tests target ``Workspace._resolve`` indirectly through ``write``/``read``
and via direct invocation, and include a hypothesis property test asserting
that no input string can ever produce a successful write outside the base.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from modules.storage import PathOutsideWorkspace, Workspace


def test_path_traversal_dotdot_blocked(workspace: Workspace, agent: str) -> None:
    with pytest.raises(PathOutsideWorkspace):
        workspace.write(agent, "../escape.txt", b"x")
    with pytest.raises(PathOutsideWorkspace):
        workspace.write(agent, "a/../../escape.txt", b"x")


def test_path_traversal_absolute_blocked(workspace: Workspace, agent: str) -> None:
    with pytest.raises(PathOutsideWorkspace):
        workspace.write(agent, "/etc/passwd", b"x")


def test_path_traversal_empty_blocked(workspace: Workspace, agent: str) -> None:
    with pytest.raises(PathOutsideWorkspace):
        workspace.write(agent, "", b"x")


def test_path_traversal_symlink_blocked(
    workspace: Workspace, agent: str, storage_root: Path, tmp_path: Path
) -> None:
    # Create a symlink inside the agent's workspace pointing outside.
    agent_dir = storage_root / "companies" / "acme" / "workspaces" / agent
    outside = tmp_path / "outside"
    outside.mkdir()
    (agent_dir / "link").symlink_to(outside)
    with pytest.raises(PathOutsideWorkspace):
        workspace.write(agent, "link/evil.txt", b"x")
    with pytest.raises(PathOutsideWorkspace):
        workspace.read_own(agent, "link/evil.txt")


def test_symlink_pointing_inside_still_refused_for_writes(
    workspace: Workspace, agent: str, storage_root: Path
) -> None:
    agent_dir = storage_root / "companies" / "acme" / "workspaces" / agent
    real_target = agent_dir / "real.txt"
    real_target.write_bytes(b"orig")
    (agent_dir / "alias").symlink_to(real_target)
    with pytest.raises(PathOutsideWorkspace):
        workspace.write(agent, "alias", b"new")


@given(
    relative=st.text(
        alphabet=st.characters(blacklist_characters="\x00"),
        min_size=0,
        max_size=64,
    )
)
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_resolve_property_never_escapes(
    workspace: Workspace, agent: str, storage_root: Path, relative: str
) -> None:
    """Either ``_resolve`` raises, or it returns a path inside the agent's base."""
    base = (
        storage_root / "companies" / "acme" / "workspaces" / agent
    ).resolve()
    try:
        target = workspace._resolve(agent, relative)
    except (PathOutsideWorkspace, OSError, ValueError):
        return
    # If it succeeded, the target MUST be inside base.
    assert str(target).startswith(str(base) + "/") or target == base
