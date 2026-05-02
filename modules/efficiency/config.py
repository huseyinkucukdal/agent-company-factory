"""Configuration for detector thresholds.

Defaults live in code (no TOML for round 1). Per-company override is a
round-2 feature; for now :func:`default_config` is the single source of
truth and :class:`EfficiencyService` accepts an override at construction.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopToolRepeatConfig:
    window_seconds: int = 5 * 60
    repeat_threshold: int = 4


@dataclass(frozen=True)
class StallNoEventConfig:
    window_seconds: int = 15 * 60  # real seconds; PLAN says "company-minutes"
    min_prior_events: int = 1  # only flag agents that *did* something before


@dataclass(frozen=True)
class ApprovalsBacklogConfig:
    min_age_seconds: int = 30 * 60  # threshold for *any* pending older than this
    min_count: int = 1


@dataclass(frozen=True)
class CostNoOutputConfig:
    window_seconds: int = 60 * 60
    min_spend_usd: float = 5.0


@dataclass(frozen=True)
class ToolErrorStormConfig:
    window_seconds: int = 10 * 60
    min_calls: int = 10
    error_rate: float = 0.30


@dataclass(frozen=True)
class OrgManagerBottleneckConfig:
    min_total_pending: int = 5
    top1_share: float = 0.60


@dataclass(frozen=True)
class EfficiencyConfig:
    """Bundled default thresholds for every R1 detector."""

    loop_tool_repeat: LoopToolRepeatConfig = LoopToolRepeatConfig()
    stall_no_event: StallNoEventConfig = StallNoEventConfig()
    approvals_backlog: ApprovalsBacklogConfig = ApprovalsBacklogConfig()
    cost_no_output: CostNoOutputConfig = CostNoOutputConfig()
    tool_error_storm: ToolErrorStormConfig = ToolErrorStormConfig()
    org_manager_bottleneck: OrgManagerBottleneckConfig = OrgManagerBottleneckConfig()

    @property
    def max_window_seconds(self) -> int:
        """Largest window any detector cares about — used to size buffers."""
        return max(
            self.loop_tool_repeat.window_seconds,
            self.stall_no_event.window_seconds,
            self.cost_no_output.window_seconds,
            self.tool_error_storm.window_seconds,
        )


def default_config() -> EfficiencyConfig:
    return EfficiencyConfig()
