"""Detector registry for the Efficiency module's R1 release."""
from __future__ import annotations

from . import (
    approvals_backlog,
    cost_no_output,
    loop_tool_repeat,
    org_manager_bottleneck,
    stall_no_event,
    tool_error_storm,
)
from .base import Detector, DetectorContext

R1_DETECTORS: list[tuple[str, Detector]] = [
    (loop_tool_repeat.CODE, loop_tool_repeat.detect),
    (stall_no_event.CODE, stall_no_event.detect),
    (approvals_backlog.CODE, approvals_backlog.detect),
    (cost_no_output.CODE, cost_no_output.detect),
    (tool_error_storm.CODE, tool_error_storm.detect),
    (org_manager_bottleneck.CODE, org_manager_bottleneck.detect),
]


__all__ = [
    "R1_DETECTORS",
    "Detector",
    "DetectorContext",
]
