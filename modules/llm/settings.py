"""Settings for the LLM module \u2014 read from environment, per-provider.

Self-contained: a plain :class:`pydantic.BaseModel` plus :meth:`from_env`.
We avoid pulling ``pydantic-settings`` just for one dataclass; the master
plan calls for it eventually but every existing module reads ``os.environ``
directly today.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LLMProvider(StrEnum):
    MOCK = "mock"
    GITHUB_MODELS = "github_models"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class LLMSettings(BaseModel):
    """Picks a provider and supplies its credentials.

    Construct from the environment::

        settings = LLMSettings.from_env()

    Or override directly for tests::

        LLMSettings(provider=LLMProvider.MOCK)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: LLMProvider = LLMProvider.MOCK
    """Which backend to dispatch to. Default: ``mock`` (no network)."""

    model: str = "gpt-4o-mini"
    """Model identifier. Provider-specific. Examples:

    * ``gpt-4o-mini`` / ``gpt-4o`` / ``Phi-3.5-mini-instruct`` (GitHub Models)
    * ``claude-3-5-sonnet-latest`` / ``claude-3-5-haiku-latest`` (Anthropic)
    """

    timeout_seconds: float = 30.0
    max_tokens: int = Field(default=2048, ge=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tool_loops: int = Field(default=24, ge=1)
    """Hard cap on agentic tool-use rounds per LLM turn.

    Bounds runaway loops while leaving headroom for legitimate multi-tool
    turns (delegation + workspace write + propose_hire + reply, etc.).
    Override via ``LLM_MAX_TOOL_LOOPS``. Was 8 historically — too low.
    """

    github_token: str | None = None
    """GitHub PAT with the ``models:read`` scope. Used by ``github_models``."""

    github_models_base_url: str = "https://models.github.ai/inference"

    anthropic_api_key: str | None = None
    """Anthropic API key. Used by ``anthropic``."""

    anthropic_base_url: str | None = None
    """Optional override (e.g. proxy / Bedrock)."""

    openai_api_key: str | None = None
    """OpenAI API key (``sk-...``). Used by the ``openai`` provider."""

    openai_base_url: str = "https://api.openai.com/v1"
    """Override for OpenAI-compatible endpoints (Azure, proxies, local)."""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMSettings:
        e: Mapping[str, str] = env if env is not None else os.environ
        provider_raw = e.get("LLM_PROVIDER", LLMProvider.MOCK.value).strip()
        try:
            provider = LLMProvider(provider_raw)
        except ValueError:
            provider = LLMProvider.MOCK

        def _f(key: str, default: float) -> float:
            raw = e.get(key)
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _i(key: str, default: int) -> int:
            raw = e.get(key)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        return cls(
            provider=provider,
            model=e.get("LLM_MODEL") or "gpt-4o-mini",
            timeout_seconds=_f("LLM_TIMEOUT_SECONDS", 30.0),
            max_tokens=_i("LLM_MAX_TOKENS", 2048),
            temperature=_f("LLM_TEMPERATURE", 0.7),
            max_tool_loops=_i("LLM_MAX_TOOL_LOOPS", 24),
            github_token=e.get("GITHUB_TOKEN") or e.get("LLM_GITHUB_TOKEN"),
            github_models_base_url=(
                e.get("LLM_GITHUB_MODELS_BASE_URL")
                or "https://models.github.ai/inference"
            ),
            anthropic_api_key=e.get("ANTHROPIC_API_KEY")
            or e.get("LLM_ANTHROPIC_API_KEY"),
            anthropic_base_url=e.get("LLM_ANTHROPIC_BASE_URL") or None,
            openai_api_key=e.get("OPENAI_API_KEY")
            or e.get("LLM_OPENAI_API_KEY"),
            openai_base_url=(
                e.get("LLM_OPENAI_BASE_URL")
                or e.get("OPENAI_BASE_URL")
                or "https://api.openai.com/v1"
            ),
        )


__all__ = ["LLMProvider", "LLMSettings"]
