"""Agent Runtime exception hierarchy."""
from __future__ import annotations


class AgentError(Exception):
    code: str = "agent_error"


class PersonaLoadError(AgentError):
    code = "persona_load"


class LLMFailure(AgentError):
    code = "llm_failure"


class TurnTimeout(AgentError):
    code = "turn_timeout"
