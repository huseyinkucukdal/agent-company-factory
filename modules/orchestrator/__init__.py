"""Orchestrator — per-company message routing & health watchdog (Module 11)."""
from __future__ import annotations

from .exceptions import (
    InvalidTarget,
    MessageTooLarge,
    OrchestratorError,
    RedisUnavailable,
)
from .health_monitor import UNHEALTHY_CUTOFF, compute_score
from .loop_detector import LoopDetector, LoopDetectorConfig
from .models import (
    AgentHealth,
    DeadLetter,
    IncomingMessage,
    MessageEnvelope,
    MessageKind,
    SendResult,
)
from .orchestrator import Orchestrator
from .protocols import AgentHandle, MessageQueue
from .rate_limit import RateLimitConfig, RateLimiter
from .streams import InMemoryMessageQueue

__all__ = [
    "UNHEALTHY_CUTOFF",
    "AgentHandle",
    "AgentHealth",
    "DeadLetter",
    "InMemoryMessageQueue",
    "IncomingMessage",
    "InvalidTarget",
    "LoopDetector",
    "LoopDetectorConfig",
    "MessageEnvelope",
    "MessageKind",
    "MessageQueue",
    "MessageTooLarge",
    "Orchestrator",
    "OrchestratorError",
    "RateLimitConfig",
    "RateLimiter",
    "RedisUnavailable",
    "SendResult",
    "compute_score",
]
